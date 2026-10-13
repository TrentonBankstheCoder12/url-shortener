from app import codes, service


def create(client, url="https://example.com/page", **extra):
    return client.post("/api/links", json={"url": url, **extra})


# --- create & redirect ---------------------------------------------------


def test_create_returns_base62_code_and_short_url(client):
    r = create(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["code"]) == 7 and set(body["code"]) <= set(codes.ALPHABET)
    assert body["short_url"] == f"http://sho.rt/{body['code']}"
    assert body["manage_token"]


def test_redirect_is_307_to_target(client):
    code = create(client, "https://example.com/a?b=1").json()["code"]
    r = client.get(f"/{code}")
    assert r.status_code == 307
    assert r.headers["location"] == "https://example.com/a?b=1"


def test_unknown_code_is_404(client):
    assert client.get("/nope123").status_code == 404
    assert client.get("/api/links/nope123").status_code == 404


def test_url_validation_rejects_dangerous_schemes(client):
    for bad in ["javascript:alert(1)", "data:text/html,hi", "ftp://x.com", "not a url"]:
        r = create(client, bad)
        assert r.status_code == 422, bad


# --- aliases -------------------------------------------------------------


def test_custom_alias_and_conflict(client):
    r = create(client, alias="my-link")
    assert r.status_code == 201 and r.json()["code"] == "my-link"
    r2 = create(client, "https://other.com", alias="my-link")
    assert r2.status_code == 409
    assert client.get("/my-link").headers["location"] == "https://example.com/page"


def test_invalid_and_reserved_aliases_rejected(client):
    for alias in ["ab", "has space", "x" * 33, "docs", "API", "bad/slash"]:
        assert create(client, alias=alias).status_code == 422, alias


def test_random_code_collision_is_retried(client, monkeypatch):
    seq = iter(["AAAAAAA", "AAAAAAA", "BBBBBBB"])
    monkeypatch.setattr(service.codes, "random_code", lambda _n: next(seq))
    assert create(client).json()["code"] == "AAAAAAA"
    # Second create collides on AAAAAAA, rolls back, and succeeds with BBBBBBB.
    assert create(client).json()["code"] == "BBBBBBB"


def test_gives_up_after_repeated_collisions(client, monkeypatch):
    monkeypatch.setattr(service.codes, "random_code", lambda _n: "SAMECODE")
    assert create(client).status_code == 201
    assert create(client).status_code == 503


# --- expiry --------------------------------------------------------------


def test_link_expires_with_410(client, clock):
    code = create(client, expires_in_seconds=60).json()["code"]
    assert client.get(f"/{code}").status_code == 307  # also warms the cache
    clock.advance(seconds=59)
    assert client.get(f"/{code}").status_code == 307
    clock.advance(seconds=1)
    assert client.get(f"/{code}").status_code == 410  # expired even though it was cached


def test_expiry_must_be_positive(client):
    assert create(client, expires_in_seconds=0).status_code == 422


# --- analytics -----------------------------------------------------------


def test_click_counting_and_analytics(client, clock):
    code = create(client).json()["code"]
    client.get(f"/{code}", headers={"Referer": "https://news.ycombinator.com/item?id=1"})
    client.get(f"/{code}", headers={"Referer": "https://news.ycombinator.com/other"})
    clock.advance(days=1)
    client.get(f"/{code}", headers={"Referer": "https://twitter.com/x/status/1"})
    client.get(f"/{code}")

    assert client.get(f"/api/links/{code}").json()["click_count"] == 4
    stats = client.get(f"/api/links/{code}/stats").json()
    assert stats["total_clicks"] == 4
    assert stats["clicks_per_day"] == [{"day": "2026-01-01", "clicks": 2}, {"day": "2026-01-02", "clicks": 2}]
    referrers = {r["referrer"]: r["clicks"] for r in stats["top_referrers"]}
    # Referrers are reduced to the host so private paths/query strings are not stored.
    assert referrers == {"news.ycombinator.com": 2, "twitter.com": 1, "direct": 1}


def test_stats_window_excludes_old_clicks(client, clock):
    code = create(client).json()["code"]
    client.get(f"/{code}")
    clock.advance(days=10)
    client.get(f"/{code}")
    stats = client.get(f"/api/links/{code}/stats", params={"days": 7}).json()
    assert sum(d["clicks"] for d in stats["clicks_per_day"]) == 1
    assert stats["total_clicks"] == 2


# --- cache ---------------------------------------------------------------


def test_redirects_are_served_from_cache(client):
    code = create(client).json()["code"]
    client.get(f"/{code}")
    client.get(f"/{code}")
    client.get(f"/{code}")
    cache = client.get("/health").json()["cache"]
    assert cache["misses"] == 1 and cache["hits"] == 2


def test_delete_invalidates_cache_and_requires_token(client):
    body = create(client).json()
    code, token = body["code"], body["manage_token"]
    assert client.get(f"/{code}").status_code == 307  # now cached
    assert code in client.app.state.cache

    assert client.delete(f"/api/links/{code}", headers={"X-Manage-Token": "wrong"}).status_code == 403
    assert client.get(f"/{code}").status_code == 307

    assert client.delete(f"/api/links/{code}", headers={"X-Manage-Token": token}).status_code == 204
    assert code not in client.app.state.cache
    assert client.get(f"/{code}").status_code == 404  # not served from a stale cache entry


def test_deleted_alias_can_be_reused(client):
    token = create(client, alias="reuse-me").json()["manage_token"]
    client.delete("/api/links/reuse-me", headers={"X-Manage-Token": token})
    r = create(client, "https://new.example.com", alias="reuse-me")
    assert r.status_code == 201
    assert client.get("/reuse-me").headers["location"] == "https://new.example.com"


# --- rate limiting -------------------------------------------------------


def test_rate_limit_returns_429_with_retry_after(make_client):
    client = make_client(rate_limit_capacity=3, rate_limit_refill_per_sec=0.5)
    assert [create(client).status_code for _ in range(3)] == [201, 201, 201]
    r = create(client)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_rate_limit_ignores_spoofed_forwarded_for_by_default(make_client):
    client = make_client(rate_limit_capacity=1, rate_limit_refill_per_sec=0.01)
    assert create(client).status_code == 201
    r = client.post("/api/links", json={"url": "https://a.com"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 429


def test_redirects_are_not_rate_limited(make_client):
    client = make_client(rate_limit_capacity=1, rate_limit_refill_per_sec=0.01)
    code = create(client).json()["code"]
    assert all(client.get(f"/{code}").status_code == 307 for _ in range(5))
