# Walkthrough: URL Shortener

A guide to how this project works and why it was built this way. Read it top to bottom, then try to explain each section out loud without looking.

## 1. Request flow

- **Create** (`POST /api/links`): rate-limit check → validate URL and alias → insert row → return the code and a one-time manage token.
- **Redirect** (`GET /{code}`): look in the LRU cache → on a miss, load from the DB and cache it → check expiry → record the click → `307` to the target.
- **Delete**: verify the manage token → delete the row and its clicks → commit → evict from the cache.

The redirect route is registered *last* so `/{code}` cannot swallow `/api/...`, `/docs` or `/health`. Those names are also reserved as aliases (`codes.RESERVED`).

Code: `app/main.py` (HTTP), `app/service.py` (logic).

## 2. Base62 codes and collisions

Base62 uses `0-9a-zA-Z`: URL-safe with no escaping. 7 characters give 62^7 ≈ 3.5 trillion codes.

There are two common ways to produce them:

| | **Counter-based** (`encode(auto_increment_id)`) | **Random** (what this project uses) |
|---|---|---|
| Collisions | Never: every id is unique | Possible, so must detect and retry |
| Guessable | Yes: `/abc` → try `/abd`. Leaks volume and lets people enumerate every link | No (`secrets` is a CSPRNG) |
| Scaling | Needs a single counter, or pre-allocated id ranges per server | No coordination needed |
| Length | Grows slowly (short at first) | Fixed |

I chose random because enumerability matters: short links often point to invoices, password resets or shared documents. `encode`/`decode` are still in `app/codes.py` (with a round-trip test) to show the counter approach.

**Collision handling:** generate a code, `INSERT`, and let the database's `UNIQUE` constraint decide. On `IntegrityError`, roll back and try a new code, up to 5 times, then return `503`. With a million links in a 3.5-trillion space the chance of one collision is about 1 in 3.5 million, so five in a row essentially never happens. If the table got large enough to make retries common, I'd increase `CODE_LENGTH`.

**Why not "SELECT to check, then INSERT"?** Two requests can both check, both see "free", and both insert. The constraint is the only check that is atomic. A test forces a collision with `monkeypatch` and checks the retry.

Custom aliases use the same insert, but only one attempt: a conflict is returned to the user as `409`.

## 3. Why 307 and not 301

A `301 Moved Permanently` is cached by browsers indefinitely. After the first visit, the browser goes straight to the target, so:

- later clicks never reach us (analytics undercount)
- an expired or deleted link keeps working for that user forever

`302` would also work, but `307` additionally guarantees the method is not changed on redirect. The cost is one extra round trip to our server per click, which is the point of an analytics-tracking shortener.

## 4. URL validation

`app/validation.py` uses an **allow-list** (`http`, `https`), not a block-list. Blocking `javascript:` alone misses `data:`, `vbscript:`, `file:` and anything invented later. It also rejects:

- whitespace and control characters (`java\tscript:` is still treated as `javascript:` by some browsers)
- missing hosts (`http://`), invalid ports (`:99999`), URLs over 2048 chars

Validation runs in the Pydantic schema, so a bad URL returns `422` before any business logic runs.

## 5. The LRU cache and invalidation

Redirects are read-heavy and the code → URL mapping is effectively immutable, so `app/cache.py` keeps up to `CACHE_SIZE` entries in an `OrderedDict`. `get` moves a key to the end (most recently used); `put` evicts from the front when full. A `threading.Lock` guards it because FastAPI runs sync endpoints in a thread pool.

What is cached: `link_id`, `target_url`, `expires_at`. Click counts are **not** cached; they change on every request and go straight to the DB.

**Invalidation rules:**

- **Delete:** evict *after* the DB commit. If we evicted first, a redirect arriving in between could read the not-yet-deleted row and put it back in the cache, and the deleted link would keep working.
- **Expiry:** checked on every request, including cache hits, against `expires_at` stored in the entry. No background job is needed; the entry is evicted when it is found expired.
- **Create:** nothing to do. A new code cannot be in the cache (a miss for an unknown code is not cached).

`/health` exposes hits and misses; a test asserts 1 miss then 2 hits for three redirects.

## 6. Rate limiting with a token bucket

Each client IP has a bucket of `capacity` tokens; each request spends one; tokens refill at `refill_per_sec`. This allows short bursts but caps the sustained rate. A fixed window ("10 per minute, reset on the minute") lets a client send 20 requests in two seconds across the boundary.

Refill is computed **lazily**: on each request, add `elapsed * rate` tokens (capped at capacity). No timers or background threads. When denied, the API returns `429` with `Retry-After`.

Details worth mentioning:

- Only writes (create, delete) are limited. Redirects are what the service is *for*.
- The client IP comes from the socket, not `X-Forwarded-For`, unless `TRUST_PROXY_HEADERS=true`. Otherwise anyone could bypass the limiter by sending a fake header (there is a test for that).
- The bucket map is capped at 10,000 clients (LRU) so a flood of distinct IPs cannot exhaust memory.
- The clock is injectable, so tests advance time instead of sleeping.

Code: `app/ratelimit.py`.

## 7. Analytics without lost updates

Each redirect does two things in one transaction:

1. `UPDATE links SET click_count = click_count + 1`: the increment happens **inside the database**. Reading the count in Python, adding one and writing it back would lose clicks when two redirects run concurrently.
2. `INSERT` a `Click` row with the time and the referrer **host**. Full referrer URLs can contain private paths or tokens; the host is all the analytics need.

Clicks per day is a `GROUP BY date(clicked_at)` over a window (`?days=30`); top referrers is a `GROUP BY referrer`. The suite runs on SQLite and was also run against Postgres 16 via `TEST_DATABASE_URL`, which caught a real portability bug: grouping by `coalesce(referrer, 'direct')` fails on Postgres because each literal becomes a separate bind parameter. The fix groups on the raw column and labels `NULL` as "direct" in Python.

## 8. Ownership and deletion

There are no user accounts, so who may delete a link? At creation the API returns a random `manage_token` once. Only its SHA-256 hash is stored, so a database leak does not reveal working tokens. Delete compares hashes with `hmac.compare_digest` (constant time, so response timing does not leak how many characters matched).

## 9. App factory and lifespan

`create_app(settings, clock)` builds a fresh app with its own engine, cache and limiter, created in the FastAPI **lifespan** handler (the replacement for the deprecated `@app.on_event("startup")`). Tests create one app per test with in-memory SQLite and a fake clock, so tests never share state. `app = create_app()` at module level is what uvicorn runs.

## 10. Known limitation (be ready to discuss this)

The cache and rate limiter live **inside one process**. Run four uvicorn workers or three containers and:

- deleting a link evicts it from one process's cache; the others keep redirecting until LRU pressure evicts it
- each process has its own buckets, so a client gets roughly N× the intended rate limit

The fix is shared state: Redis for both (with pub/sub or short TTLs for invalidation, and an atomic Lua script for the token bucket). A smaller mitigation without Redis is a TTL on cache entries, which bounds how long a stale entry can survive.

A second limitation: every redirect does a synchronous `INSERT`, so analytics writes sit on the hot path. At high volume, push click events to a queue and aggregate in the background.

## Likely interview questions

1. **Random codes or a counter?** Random avoids enumeration and needs no coordination; the cost is rare collisions handled by a `UNIQUE` constraint plus retry (section 2).
2. **How do you handle two requests creating the same alias at the same time?** Both insert; the constraint lets one win and the other gets `IntegrityError` → `409`. No check-then-insert race.
3. **Why 307 over 301?** 301 is cached permanently by browsers, which breaks analytics, expiry and deletion (section 3).
4. **How do you keep the cache correct?** Cache only immutable fields, check expiry on every hit, evict on delete *after* commit. In multiple processes you need a shared cache or pub/sub invalidation (sections 5, 10).
5. **Token bucket vs. fixed window?** The bucket allows controlled bursts and has no boundary spike; lazy refill makes it O(1) with no timers (section 6).
6. **How do you count clicks under concurrency?** Atomic `SET count = count + 1` in SQL, not read-modify-write in Python (section 7).
7. **How would you scale to 100k redirects per second?** CDN or edge cache for redirects, Redis for code lookups, async click pipeline (queue → batch inserts → rollup tables), and read replicas.

## Try extending it

- Add a TTL to cache entries and a test that a stale entry disappears after it
- Move click recording to a FastAPI `BackgroundTask` and measure redirect latency
- Add a QR-code endpoint for each link
- Implement the token bucket in Redis with a Lua script and run two app instances against it
