from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth as auth_api
from .api import wallets as wallets_api
from .api import profile as profile_api
from .api import trades as trades_api
from .core.config import get_settings
from .core.logging import setup_logging
from .db.session import Base, engine

setup_logging()
settings = get_settings()

app = FastAPI(title="BTC Oracle", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    # In production use Alembic. For local dev / docker compose, auto-create.
    Base.metadata.create_all(bind=engine)


@app.get("/healthz")
def healthz():
    return {"ok": True, "env": settings.APP_ENV, "live_trading": settings.LIVE_TRADING}


app.include_router(auth_api.router)
app.include_router(wallets_api.router)
app.include_router(profile_api.router)
app.include_router(trades_api.router)
