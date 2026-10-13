from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from .validation import InvalidURL, validate_url

TEN_YEARS = 10 * 365 * 24 * 3600


class LinkCreate(BaseModel):
    url: str = Field(examples=["https://example.com/some/very/long/path"])
    alias: str | None = Field(default=None, description="Custom code, 3-32 chars of [A-Za-z0-9_-]")
    expires_in_seconds: int | None = Field(default=None, gt=0, le=TEN_YEARS)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        try:
            return validate_url(v)
        except InvalidURL as e:
            raise ValueError(str(e)) from None


class LinkOut(BaseModel):
    code: str
    short_url: str
    target_url: str
    created_at: datetime
    expires_at: datetime | None
    click_count: int


class LinkCreated(LinkOut):
    # Returned exactly once, at creation. Only its hash is stored, like a password.
    manage_token: str


class DayCount(BaseModel):
    day: date
    clicks: int


class ReferrerCount(BaseModel):
    referrer: str
    clicks: int


class LinkStats(BaseModel):
    code: str
    total_clicks: int
    clicks_per_day: list[DayCount]
    top_referrers: list[ReferrerCount]
