"""24/7 background loops."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.engine import forecast_for_window
from ..core.config import get_settings
from ..core.kms import SealedSecret, vault
from ..db.session import SessionLocal
from ..models import Prediction, Trade, TradingProfile, User, Wallet
from ..services.polymarket import (
    OrderRequest,
    PolymarketClient,
    current_window_ts,
    live_submit,
    next_window_ts,
    paper_submit,
)
from ..services.risk import decide
from .celery_app import celery_app

log = logging.getLogger(__name__)
settings = get_settings()
_r = redis.from_url(settings.REDIS_URL, decode_responses=True)
PRED_KEY = "btc_oracle:pred:{ws}"
LOCK_KEY = "btc_oracle:lock:{ws}:{user_id}"


# ───────────────────────── Prediction loop ─────────────────────────

@celery_app.task(name="app.workers.tasks.run_prediction_cycle")
def run_prediction_cycle() -> dict:
    """Forecast the NEXT 5-min window and cache to Redis + DB."""
    ws = next_window_ts()
    try:
        fc = asyncio.run(forecast_for_window(ws))
    except Exception as e:
        log.exception("forecast failed: %s", e)
        return {"ok": False, "error": str(e)}

    _r.setex(PRED_KEY.format(ws=ws), 600, json.dumps(fc.to_dict()))

    db: Session = SessionLocal()
    try:
        pred = Prediction(
            window_ts=ws,
            p_up=fc.p_up,
            ml_p_up=fc.ml_p_up,
            swarm_p_up=fc.swarm_p_up,
            swarm_votes={"votes": [v.__dict__ for v in fc.votes]},
            btc_price=fc.btc_price,
            features=fc.features,
        )
        db.add(pred)
        db.commit()
    finally:
        db.close()

    log.info("forecast window=%s p_up=%.3f btc=%.2f", ws, fc.p_up, fc.btc_price)
    return {"ok": True, "window_ts": ws, "p_up": fc.p_up}


# ───────────────────────── Trade loop ─────────────────────────

@celery_app.task(name="app.workers.tasks.trade_tick")
def trade_tick() -> dict:
    """Fire ~10s before the current window closes."""
    now = int(time.time())
    ws = current_window_ts(now)
    seconds_left = (ws + 300) - now
    if seconds_left > 15 or seconds_left < 5:
        return {"ok": True, "skipped": True, "seconds_left": seconds_left}

    cached = _r.get(PRED_KEY.format(ws=ws + 300))  # forecast was for "next" window
    if not cached:
        # fall back to current-window forecast
        cached = _r.get(PRED_KEY.format(ws=ws))
    if not cached:
        log.info("no forecast cached for window=%s, skipping", ws)
        return {"ok": True, "no_forecast": True}
    fc = json.loads(cached)

    poly = PolymarketClient()
    market = asyncio.run(poly.find_btc_market(ws))
    if not market:
        log.warning("no Polymarket BTC market found for window=%s", ws)
        return {"ok": True, "no_market": True}

    placed = 0
    db: Session = SessionLocal()
    try:
        users_with_profile = db.execute(
            select(User)
            .join(TradingProfile, TradingProfile.user_id == User.id)
            .where(TradingProfile.auto_trade_enabled == True)
        ).scalars().all()

        for user in users_with_profile:
            try:
                placed += _attempt_user_trade(db, user, ws, fc, market)
            except Exception:
                log.exception("user %s trade failed", user.id)
        db.commit()
    finally:
        db.close()

    return {"ok": True, "window_ts": ws, "placed": placed}


def _attempt_user_trade(db: Session, user: User, ws: int, fc: dict, market) -> int:
    profile = user.profile
    if not profile:
        return 0

    # Idempotency: one trade per user per window
    lock = LOCK_KEY.format(ws=ws, user_id=user.id)
    if not _r.set(lock, "1", nx=True, ex=600):
        return 0
    decision = decide(db, profile, fc["p_up"], market.up_best_ask, market.down_best_ask)
    if not decision.should_trade:
        log.info("user=%s skip: %s", user.id, decision.reason)
        return 0

    token_id = market.up_token_id if decision.side == "up" else market.down_token_id
    ask = market.up_best_ask if decision.side == "up" else market.down_best_ask

    req = OrderRequest(
        token_id=token_id, side="BUY", price=ask, size=decision.stake_usdc
    )

    use_real = settings.LIVE_TRADING and not profile.paper_only and user.wallet
    if use_real:
        try:
            secret_bytes = vault.open(
                SealedSecret.from_dict(user.wallet.sealed), aad=str(user.id).encode()
            )
            if user.wallet.mode == "private_key":
                pk = secret_bytes.decode()
                result = asyncio.run(live_submit(req, pk, user.wallet.funder))
            else:
                # API-key live-order path is not yet implemented.
                log.warning("api_key mode live trading not implemented; falling back to paper")
                use_real = False
                result = asyncio.run(paper_submit(req, ask))
        except Exception as e:
            log.exception("live order failed for user=%s: %s", user.id, e)
            result = asyncio.run(paper_submit(req, ask))
            use_real = False
    else:
        result = asyncio.run(paper_submit(req, ask))

    trade = Trade(
        user_id=user.id,
        window_ts=ws,
        market_slug=market.slug,
        side=decision.side,
        stake_usdc=decision.stake_usdc,
        avg_price=result.avg_price,
        tokens_filled=result.filled_size / max(result.avg_price, 1e-6),
        is_paper=not use_real,
        status="filled" if result.success else "error",
        order_meta=result.raw,
    )
    db.add(trade)
    return 1


# ───────────────────────── Reconciliation ─────────────────────────

@celery_app.task(name="app.workers.tasks.reconcile_open_trades")
def reconcile_open_trades() -> dict:
    """Resolve filled trades after their window closes by checking real BTC
    close vs window-open price. This works for both paper and live trades
    (live PnL is the same payout structure)."""
    db: Session = SessionLocal()
    try:
        open_trades = db.execute(
            select(Trade).where(Trade.status == "filled")
        ).scalars().all()

        from ..ai.market_data import fetch_klines

        # Fetch klines once; 360 x 1-min candles covers every open trade window.
        try:
            klines = asyncio.run(fetch_klines("1m", 360))
        except Exception:
            log.exception("klines fetch failed; skipping reconcile")
            return {"ok": False}

        df = klines
        df_ts = (df["open_time"].astype("int64") // 10**9).values

        for t in open_trades:
            close_ts = t.window_ts + 300
            if int(time.time()) < close_ts + 30:
                continue
            # find open price (at window_ts) and close price (at close_ts)
            open_idx = next((i for i, v in enumerate(df_ts) if v >= t.window_ts), None)
            close_raw = next((i for i, v in enumerate(df_ts) if v >= close_ts), None)
            if open_idx is None or close_raw is None or close_raw == 0:
                continue
            close_idx = close_raw - 1
            open_p = float(df["open"].iloc[open_idx])
            close_p = float(df["close"].iloc[close_idx])
            went_up = close_p > open_p
            won = (t.side == "up" and went_up) or (t.side == "down" and not went_up)
            if won:
                t.status = "won"
                # binary payout: each token pays $1
                t.pnl_usdc = round(t.tokens_filled - t.stake_usdc, 4)
            else:
                t.status = "lost"
                t.pnl_usdc = round(-t.stake_usdc, 4)
            t.resolved_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return {"ok": True}
