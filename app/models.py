from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Timestamps are stored as naive UTC. SQLite has no real timezone type, so being explicit
# about "always UTC" is simpler than relying on driver-specific timezone handling.


class Link(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The UNIQUE constraint is what actually guarantees no two links share a code,
    # even when two requests race to insert the same one.
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    target_url: Mapped[str] = mapped_column(Text)
    is_custom: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    # SHA-256 of the secret returned at creation; required to delete the link.
    manage_token_hash: Mapped[str] = mapped_column(String(64))

    clicks: Mapped[list[Click]] = relationship(back_populates="link", cascade="all, delete-orphan")


class Click(Base):
    """One row per redirect, so analytics can be sliced any way later (per day, per referrer...)."""

    __tablename__ = "clicks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("links.id", ondelete="CASCADE"), index=True)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    referrer: Mapped[str | None] = mapped_column(String(512), nullable=True)

    link: Mapped[Link] = relationship(back_populates="clicks")
