"""All tunables come from environment variables so the same image runs in dev, CI and prod."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./shortener.db"
    base_url: str = "http://localhost:8000"
    code_length: int = 7
    cache_size: int = 1024
    # Token bucket for link creation: burst of `capacity`, refilled at `refill_per_sec`.
    rate_limit_capacity: int = 10
    rate_limit_refill_per_sec: float = 1.0
    # Only trust X-Forwarded-For when running behind a proxy you control.
    trust_proxy_headers: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            database_url=env.get("DATABASE_URL", cls.database_url),
            base_url=env.get("BASE_URL", cls.base_url).rstrip("/"),
            code_length=int(env.get("CODE_LENGTH", cls.code_length)),
            cache_size=int(env.get("CACHE_SIZE", cls.cache_size)),
            rate_limit_capacity=int(env.get("RATE_LIMIT_CAPACITY", cls.rate_limit_capacity)),
            rate_limit_refill_per_sec=float(
                env.get("RATE_LIMIT_REFILL_PER_SEC", cls.rate_limit_refill_per_sec)
            ),
            trust_proxy_headers=env.get("TRUST_PROXY_HEADERS", "false").lower() == "true",
        )
