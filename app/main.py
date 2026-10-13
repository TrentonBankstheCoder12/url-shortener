from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from . import service
from .cache import LRUCache
from .config import Settings
from .db import Base, make_engine, make_sessionmaker
from .models import Link
from .ratelimit import TokenBucketLimiter
from .schemas import LinkCreate, LinkCreated, LinkOut, LinkStats


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        session.close()


# Module level (not inside create_app) so FastAPI can resolve the annotation by name.
SessionDep = Annotated[Session, Depends(get_session)]


def create_app(settings: Settings | None = None, clock: Callable[[], datetime] = service.utcnow) -> FastAPI:
    """App factory: tests build an isolated app (own DB, cache, limiter, clock) per test."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        Base.metadata.create_all(engine)  # fine for a demo; a real deployment would use Alembic
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.cache = LRUCache(settings.cache_size)
        app.state.limiter = TokenBucketLimiter(
            settings.rate_limit_capacity, settings.rate_limit_refill_per_sec
        )
        yield
        engine.dispose()

    app = FastAPI(
        lifespan=lifespan,
        title="URL Shortener",
        description="Base62 short links with expiry, analytics, LRU caching and rate limiting.",
        version="1.0.0",
    )
    app.state.settings = settings
    app.state.clock = clock

    @app.exception_handler(service.ShortenerError)
    def _shortener_error(_: Request, exc: service.ShortenerError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    # --- dependencies -----------------------------------------------------

    def client_ip(request: Request) -> str:
        # X-Forwarded-For is client-controlled; trusting it blindly lets anyone dodge the
        # limiter by sending a fake header. Only honour it behind a trusted reverse proxy.
        if settings.trust_proxy_headers and (fwd := request.headers.get("x-forwarded-for")):
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def rate_limit(request: Request) -> None:
        allowed, retry_after = request.app.state.limiter.allow(client_ip(request))
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )

    def to_out(link: Link) -> dict:
        return {
            "code": link.code,
            "short_url": f"{settings.base_url}/{link.code}",
            "target_url": link.target_url,
            "created_at": link.created_at,
            "expires_at": link.expires_at,
            "click_count": link.click_count,
        }

    # --- routes -----------------------------------------------------------

    @app.get("/health")
    def health(request: Request):
        cache: LRUCache = request.app.state.cache
        return {"status": "ok", "cache": {"size": len(cache), "hits": cache.hits, "misses": cache.misses}}

    @app.post("/api/links", response_model=LinkCreated, status_code=201, dependencies=[Depends(rate_limit)])
    def create_link(body: LinkCreate, session: SessionDep, request: Request):
        link, token = service.create_link(
            session,
            body.url,
            now=request.app.state.clock(),
            code_length=settings.code_length,
            alias=body.alias,
            expires_in_seconds=body.expires_in_seconds,
        )
        return {**to_out(link), "manage_token": token}

    @app.get("/api/links/{code}", response_model=LinkOut)
    def read_link(code: str, session: SessionDep):
        return to_out(service.get_link(session, code))

    @app.get("/api/links/{code}/stats", response_model=LinkStats)
    def read_stats(code: str, session: SessionDep, request: Request, days: int = Query(30, ge=1, le=365)):
        link = service.get_link(session, code)
        return service.stats(session, link, days, request.app.state.clock())

    @app.delete("/api/links/{code}", status_code=204, dependencies=[Depends(rate_limit)])
    def delete_link(
        code: str,
        session: SessionDep,
        request: Request,
        x_manage_token: Annotated[str, Header()],
    ):
        service.delete_link(session, request.app.state.cache, code, x_manage_token)
        return Response(status_code=204)

    # Registered last so it never shadows the routes above.
    @app.get("/{code}", response_class=RedirectResponse, status_code=307)
    def redirect(
        code: str, session: SessionDep, request: Request, referer: Annotated[str | None, Header()] = None
    ):
        now = request.app.state.clock()
        entry = service.resolve(session, request.app.state.cache, code, now)
        service.record_click(session, entry.link_id, referer, now)
        # 307 (not 301): browsers cache 301s forever, so later clicks would never reach us
        # (no analytics) and we could never expire or delete the link for that user.
        return RedirectResponse(entry.target_url, status_code=307)

    return app


app = create_app()
