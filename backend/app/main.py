import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth as auth_api
from .api import wallets as wallets_api
from .api import profile as profile_api
from .api import trades as trades_api
from .core.config import get_settings
from .core.logging import setup_logging

setup_logging()
settings = get_settings()
log = logging.getLogger("app.main")

app = FastAPI(title="BTC Oracle (poly-trade)", version="0.1.0")

# CORS: allow the configured frontend AND any *.up.railway.app subdomain.
# Use a regex so users can set FRONTEND_ORIGIN later without redeploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN] if settings.FRONTEND_ORIGIN else [],
    allow_origin_regex=r"https://.*\.up\.railway\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    """Create tables if a DB is reachable. NEVER crash the app at boot —
    the /healthz probe must answer 200 so Railway marks the deploy healthy
    even before the user has wired up Postgres."""
    try:
        from .db.session import Base, engine
        Base.metadata.create_all(bind=engine)
        log.info("DB ready: tables ensured")
    except Exception as e:
        log.warning("DB not reachable at startup (%s) — set DATABASE_URL", e)


@app.get("/healthz")
def healthz():
    """Liveness probe — must NEVER touch the DB."""
    return {
        "ok": True,
        "env": settings.APP_ENV,
        "live_trading": settings.LIVE_TRADING,
        "version": "0.1.0",
    }


@app.get("/")
def root():
    """Friendly landing page so visitors don't see a 404."""
    return {
        "service": "poly-trade api",
        "docs": "/docs",
        "health": "/healthz",
        "frontend_origin": settings.FRONTEND_ORIGIN,
    }


# Routers
app.include_router(auth_api.router)
app.include_router(wallets_api.router)
app.include_router(profile_api.router)
app.include_router(trades_api.router)
