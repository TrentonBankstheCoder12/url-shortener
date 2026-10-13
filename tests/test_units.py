import pytest

from app import codes
from app.cache import CachedLink, LRUCache
from app.ratelimit import TokenBucketLimiter
from app.validation import InvalidURL, validate_url


def test_base62_round_trip_property():
    for n in [0, 1, 61, 62, 63, 3843, 3844, 62**7 - 1, 10**18]:
        assert codes.decode(codes.encode(n)) == n
    assert codes.encode(61) == "Z" and codes.encode(62) == "10"


def test_random_code_uses_only_base62_alphabet():
    code = codes.random_code(7)
    assert len(code) == 7 and set(code) <= set(codes.ALPHABET)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "ftp://example.com",
        "example.com",
        "http://",
        "https://exa mple.com",
        "java\tscript:alert(1)",
        "http://example.com:99999",
        "",
    ],
)
def test_invalid_urls_are_rejected(url):
    with pytest.raises(InvalidURL):
        validate_url(url)


def test_valid_urls_are_accepted():
    assert validate_url("https://example.com/a?b=c#d") == "https://example.com/a?b=c#d"
    assert validate_url("  HTTP://Example.com:8080/x  ") == "HTTP://Example.com:8080/x"


def test_lru_evicts_least_recently_used():
    cache = LRUCache(2)
    a, b, c = (CachedLink(i, f"https://{i}.com", None) for i in range(3))
    cache.put("a", a)
    cache.put("b", b)
    assert cache.get("a") == a  # touch "a" so "b" becomes least recent
    cache.put("c", c)
    assert "b" not in cache and "a" in cache and "c" in cache


def test_token_bucket_allows_burst_then_refills():
    t = [0.0]
    limiter = TokenBucketLimiter(capacity=3, refill_per_sec=1.0, clock=lambda: t[0])
    assert [limiter.allow("ip")[0] for _ in range(4)] == [True, True, True, False]
    allowed, retry_after = limiter.allow("ip")
    assert not allowed and retry_after == pytest.approx(1.0)
    t[0] = 1.5
    assert limiter.allow("ip")[0] is True
    assert limiter.allow("other-ip")[0] is True  # buckets are per client


def test_token_bucket_never_exceeds_capacity_after_long_idle():
    t = [0.0]
    limiter = TokenBucketLimiter(capacity=2, refill_per_sec=10.0, clock=lambda: t[0])
    t[0] = 1_000_000.0
    assert [limiter.allow("ip")[0] for _ in range(3)] == [True, True, False]
