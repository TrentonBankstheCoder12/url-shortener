"""Base62 short codes.

Base62 = [0-9a-zA-Z]: URL-safe without escaping, and dense. 7 characters give
62**7 ~= 3.5 trillion possible codes.

We generate codes *randomly* (with `secrets`, so they are not guessable) and rely on the
database UNIQUE constraint to detect the rare collision; the service then retries with a new
code. See WALKTHROUGH.md for the counter-based alternative and why we did not choose it.
"""

from __future__ import annotations

import re
import secrets

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)

ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
# Paths the app itself uses; an alias with one of these names would shadow a real route.
RESERVED = {"api", "docs", "redoc", "openapi.json", "health", "static", "admin"}


def encode(n: int) -> str:
    """Integer -> base62. Used by the counter-based strategy described in the walkthrough."""
    if n < 0:
        raise ValueError("cannot encode negative numbers")
    if n == 0:
        return ALPHABET[0]
    chars = []
    while n:
        n, rem = divmod(n, BASE)
        chars.append(ALPHABET[rem])
    return "".join(reversed(chars))


def decode(s: str) -> int:
    n = 0
    for ch in s:
        n = n * BASE + ALPHABET.index(ch)
    return n


def random_code(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def is_valid_alias(alias: str) -> bool:
    return bool(ALIAS_PATTERN.match(alias)) and alias.lower() not in RESERVED
