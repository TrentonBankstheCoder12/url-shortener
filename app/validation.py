"""URL validation. A shortener that accepts any string becomes a tool for phishing and XSS:
`javascript:alert(1)` behind a trusted-looking short link would run in the victim's browser."""

from __future__ import annotations

from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048  # practical limit most browsers and proxies accept
ALLOWED_SCHEMES = {"http", "https"}


class InvalidURL(ValueError):
    pass


def validate_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        raise InvalidURL("url must not be empty")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidURL(f"url must be at most {MAX_URL_LENGTH} characters")
    # Control characters and whitespace can smuggle a different URL past naive checks
    # (e.g. "java\tscript:" is still treated as javascript: by some browsers).
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise InvalidURL("url must not contain whitespace or control characters")

    # Allow-list, not block-list: we accept http(s) only, so javascript:, data:, file:,
    # vbscript: and anything invented later are all rejected without being named.
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidURL("only http and https URLs are allowed")
    try:
        hostname = parts.hostname
        _ = parts.port  # accessing .port raises ValueError for a malformed port like ":99999"
    except ValueError:
        raise InvalidURL("url has an invalid host or port") from None
    if not hostname:
        raise InvalidURL("url must include a host")
    return url
