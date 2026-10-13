# URL Shortener

[![CI](https://github.com/TrentonBankstheCoder12/url-shortener/actions/workflows/ci.yml/badge.svg)](https://github.com/TrentonBankstheCoder12/url-shortener/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy%202-D71F00?logo=sqlalchemy&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

A **URL shortener API** built with FastAPI and SQLAlchemy 2. It looks simple, but it covers the problems behind every high-traffic, read-heavy service: generating unique IDs without collisions, caching the hot path and invalidating it correctly, rate limiting abusive clients, validating untrusted input (a short link to `javascript:` is a phishing tool), and recording analytics without losing updates. Payment links, referral links and SMS one-time links at fintech companies run on exactly this design.

## Features

- **Base62 short codes** (7 chars, ~3.5 trillion combinations) generated with `secrets`, with collision retry backed by a `UNIQUE` constraint
- **Custom aliases** with validation, reserved-word protection and `409 Conflict` on duplicates
- **Expiration:** expired links return `410 Gone`, checked on every request (cached or not)
- **307 redirects** so every click reaches the server (no permanent browser caching)
- **Click analytics:** total count, clicks per day, top referrers (host only, for privacy)
- **URL validation:** http(s) allow-list; rejects `javascript:`, `data:`, `file:`, control characters, bad hosts/ports
- **In-process LRU cache** in front of the DB for redirects, invalidated on delete
- **Per-IP token-bucket rate limiting** on writes, with `Retry-After`
- **Owner-only delete** via a one-time manage token (only its SHA-256 hash is stored)
- **SQLite by default, Postgres via `DATABASE_URL`**; Dockerfile + docker-compose included
- FastAPI **lifespan** for startup/shutdown, and an **app factory** so tests get isolated instances

## Architecture

```
                 ┌───────────────────────────────────────┐
  HTTP ────────▶ │ app/main.py   FastAPI routes          │
                 │   rate_limit dependency ──▶ ratelimit │  token bucket per IP (writes only)
                 └───────────────┬───────────────────────┘
                                 ▼
                 ┌───────────────────────────────────────┐
                 │ app/service.py  create / resolve /    │
                 │   record_click / delete / stats       │
                 └──────┬─────────────────────┬──────────┘
          GET /{code}   │                     │
                 ┌──────▼──────┐   miss   ┌───▼─────────────────────────┐
                 │ app/cache   │ ───────▶ │ app/models (SQLAlchemy 2)   │
                 │ LRU (code → │ ◀─ fill  │   Link ─< Click             │
                 │  target)    │          └───┬─────────────────────────┘
                 └─────────────┘              ▼
                  invalidated on   SQLite (default) / PostgreSQL (DATABASE_URL)
                  delete & expiry
```

## Quick start

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
# Interactive docs at http://localhost:8000/docs
```

With Postgres:

```bash
docker compose up --build
```

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./shortener.db` | e.g. `postgresql+psycopg://user:pass@host/db` |
| `BASE_URL` | `http://localhost:8000` | Prefix for `short_url` in responses |
| `CODE_LENGTH` | `7` | Length of generated codes |
| `CACHE_SIZE` | `1024` | Max entries in the LRU cache |
| `RATE_LIMIT_CAPACITY` / `RATE_LIMIT_REFILL_PER_SEC` | `10` / `1.0` | Token bucket burst size and refill rate |
| `TRUST_PROXY_HEADERS` | `false` | Use `X-Forwarded-For` for the client IP (only behind your own proxy) |

## Usage example

```bash
# Create a link with a custom alias that expires in an hour
curl -X POST localhost:8000/api/links -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/invoice/123", "alias": "inv-123", "expires_in_seconds": 3600}'
# {"code": "inv-123", "short_url": "http://localhost:8000/inv-123", ..., "manage_token": "HOlc..."}

curl -i localhost:8000/inv-123 -H 'Referer: https://google.com/search?q=x'
# HTTP/1.1 307 Temporary Redirect
# location: https://example.com/invoice/123

curl localhost:8000/api/links/inv-123/stats
# {"code": "inv-123", "total_clicks": 1,
#  "clicks_per_day": [{"day": "2026-10-13", "clicks": 1}],
#  "top_referrers": [{"referrer": "google.com", "clicks": 1}]}

curl -X DELETE localhost:8000/api/links/inv-123 -H 'X-Manage-Token: HOlc...'   # 204

curl -X POST localhost:8000/api/links -H 'Content-Type: application/json' -d '{"url": "javascript:alert(1)"}'
# 422: only http and https URLs are allowed
```

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/links` | Create a link (`url`, optional `alias`, `expires_in_seconds`). Rate limited. |
| `GET` | `/{code}` | `307` redirect; `404` unknown, `410` expired. Records a click. |
| `GET` | `/api/links/{code}` | Link details and click count |
| `GET` | `/api/links/{code}/stats?days=30` | Clicks per day and top referrers |
| `DELETE` | `/api/links/{code}` | Delete (needs `X-Manage-Token`); invalidates the cache |
| `GET` | `/health` | Liveness plus cache size / hits / misses |

## Testing

```bash
pytest -q
ruff check .

# Optional: run the same suite against Postgres
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/test pytest -q
```

35 tests cover creation, 307 redirects, URL validation (11 malicious/malformed cases), alias conflicts and reserved words, collision retry, expiry (including for cached links), click counting, per-day and referrer analytics, LRU eviction, cache hits, cache invalidation on delete, token-bucket burst/refill, `429` with `Retry-After`, and spoofed `X-Forwarded-For`. Time is injected through a fake clock, so expiry and rate-limit tests never sleep. A base62 round-trip property test checks `decode(encode(n)) == n`.

## What I'd add next

- A shared cache (Redis) with pub/sub invalidation so multiple API instances stay consistent
- Write clicks to a queue and aggregate asynchronously, so redirects never wait on an `INSERT`
- Alembic migrations instead of `create_all`
- Safe-browsing / domain blocklist checks on target URLs
- API keys with per-key quotas instead of per-IP limits

See [WALKTHROUGH.md](WALKTHROUGH.md) for the design decisions behind this project.

## License

MIT
