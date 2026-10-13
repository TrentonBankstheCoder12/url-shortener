"""Business logic: creating links, resolving codes, recording clicks, analytics.

Kept free of FastAPI so it can be read (and tested) as plain Python.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import codes
from .cache import CachedLink, LRUCache
from .models import Click, Link

MAX_CODE_ATTEMPTS = 5


class ShortenerError(Exception):
    status_code = 400


class AliasTaken(ShortenerError):
    status_code = 409


class InvalidAlias(ShortenerError):
    status_code = 422


class NotFound(ShortenerError):
    status_code = 404


class Expired(ShortenerError):
    status_code = 410  # Gone: it existed, and will not come back


class Forbidden(ShortenerError):
    status_code = 403


class CodeSpaceExhausted(ShortenerError):
    status_code = 503


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_link(
    session: Session,
    url: str,
    *,
    now: datetime,
    code_length: int,
    alias: str | None = None,
    expires_in_seconds: int | None = None,
) -> tuple[Link, str]:
    """Create a short link. Returns the link and its one-time manage token."""
    if alias is not None and not codes.is_valid_alias(alias):
        raise InvalidAlias("alias must be 3-32 characters of letters, digits, '_' or '-' and not reserved")

    token = secrets.token_urlsafe(24)
    expires_at = now + timedelta(seconds=expires_in_seconds) if expires_in_seconds else None

    # Custom alias: one attempt, conflict is the user's problem.
    # Random code: on the (rare) collision, roll back and try a fresh code.
    attempts = 1 if alias else MAX_CODE_ATTEMPTS
    for _ in range(attempts):
        link = Link(
            code=alias or codes.random_code(code_length),
            target_url=url,
            is_custom=alias is not None,
            created_at=now,
            expires_at=expires_at,
            click_count=0,
            manage_token_hash=_hash_token(token),
        )
        session.add(link)
        try:
            # We insert and let the UNIQUE constraint decide, instead of "SELECT then INSERT".
            # A check-then-insert has a race window where two requests both see "free".
            session.commit()
            return link, token
        except IntegrityError:
            session.rollback()

    if alias:
        raise AliasTaken(f"alias '{alias}' is already taken")
    raise CodeSpaceExhausted("could not allocate a unique code, try again")


def get_link(session: Session, code: str) -> Link:
    link = session.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise NotFound(f"no link with code '{code}'")
    return link


def resolve(session: Session, cache: LRUCache, code: str, now: datetime) -> CachedLink:
    """Find where a code points, using the cache first. Raises NotFound / Expired."""
    entry = cache.get(code)
    if entry is None:
        link = get_link(session, code)
        entry = CachedLink(link.id, link.target_url, link.expires_at)
        cache.put(code, entry)

    # Expiry is checked on every request, including cache hits, so a cached link
    # stops working at exactly its expiry time without needing a background sweeper.
    if entry.expires_at is not None and now >= entry.expires_at:
        cache.invalidate(code)
        raise Expired(f"link '{code}' has expired")
    return entry


def normalize_referrer(raw: str | None) -> str | None:
    """Keep just the host. Full referrer URLs can contain private paths or tokens,
    and grouping by host is what the analytics need anyway."""
    if not raw:
        return None
    host = urlsplit(raw).hostname
    return host[:512] if host else None


def record_click(session: Session, link_id: int, referrer: str | None, now: datetime) -> None:
    # `click_count = click_count + 1` is computed inside the database, so concurrent
    # redirects cannot overwrite each other's increment (a lost update).
    session.execute(update(Link).where(Link.id == link_id).values(click_count=Link.click_count + 1))
    session.add(Click(link_id=link_id, clicked_at=now, referrer=normalize_referrer(referrer)))
    session.commit()


def delete_link(session: Session, cache: LRUCache, code: str, token: str) -> None:
    link = get_link(session, code)
    if not hmac.compare_digest(link.manage_token_hash, _hash_token(token)):  # constant-time compare
        raise Forbidden("invalid manage token")
    session.execute(delete(Click).where(Click.link_id == link.id))
    session.delete(link)
    session.commit()
    # Invalidate *after* the commit. If we evicted first, a redirect arriving between the
    # eviction and the commit could read the still-present row and re-cache it, and the
    # deleted link would keep redirecting until it was evicted by LRU pressure.
    cache.invalidate(code)


def stats(session: Session, link: Link, days: int, now: datetime) -> dict:
    since = now - timedelta(days=days)
    day = func.date(Click.clicked_at)  # SQLite and Postgres both support date()
    per_day = session.execute(
        select(day, func.count())
        .where(Click.link_id == link.id, Click.clicked_at >= since)
        .group_by(day)
        .order_by(day)
    ).all()
    # Group on the raw column (NULL = no referrer) and label it in Python. Grouping on
    # coalesce(referrer, 'direct') fails on Postgres because each literal becomes a separate
    # bind parameter, so the SELECT and GROUP BY expressions no longer match.
    referrers = session.execute(
        select(Click.referrer, func.count())
        .where(Click.link_id == link.id)
        .group_by(Click.referrer)
        .order_by(func.count().desc(), Click.referrer)
        .limit(10)
    ).all()
    return {
        "code": link.code,
        "total_clicks": link.click_count,
        "clicks_per_day": [{"day": d, "clicks": n} for d, n in per_day],
        "top_referrers": [{"referrer": r or "direct", "clicks": n} for r, n in referrers],
    }
