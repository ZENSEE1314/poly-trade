"""24/7 background loops."""
from __future__ import annotations

import asyncio
import json
import logging
import random
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
SSE_CHANNEL = "btc_oracle:events"

# Only trade when the model has at least this much conviction.
# conf = abs(p_up - 0.5) * 2  →  0.30 means p_up < 0.35 or p_up > 0.65
MIN_DEMO_CONFIDENCE = 0.30


def _publish(event: dict) -> None:
    """Publish a JSON event to the SSE channel. Best-effort — never raises."""
    try:
        _r.publish(SSE_CHANNEL, json.dumps(event))
    except Exception as exc:
        log.debug("SSE publish failed: %s", exc)


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
    _publish({
        "type": "prediction",
        "window_ts": ws,
        "p_up": round(fc.p_up, 4),
        "ml_p_up": round(fc.ml_p_up, 4),
        "swarm_p_up": round(fc.swarm_p_up, 4),
        "btc_price": round(fc.btc_price, 2),
        # Include votes so the dashboard swarm panel updates without a page refresh
        "votes": {"votes": [v.__dict__ for v in fc.votes]},
    })

    # Piggyback: place a simulated paper trade once per 5-min window.
    # Using Redis lock so the 60s prediction loop only fires one trade per window.
    trade_lock = f"btc_oracle:demo:{ws}"
    if _r.set(trade_lock, "1", nx=True, ex=600):
        try:
            _place_demo_trades(ws, fc.p_up)
        except Exception as exc:
            log.warning("demo trade placement failed: %s", exc)

    return {"ok": True, "window_ts": ws, "p_up": fc.p_up}


def _place_demo_trades(ws: int, p_up: float) -> int:
    """Insert one simulated paper trade per user for the given window.

    Only trades when model confidence exceeds MIN_DEMO_CONFIDENCE so we
    never buy into a near-coin-flip.  Called from run_prediction_cycle.
    """
    conf = abs(p_up - 0.5) * 2          # 0 = no edge, 1 = maximum conviction
    if conf < MIN_DEMO_CONFIDENCE:
        log.info("demo tick skipped: conf=%.2f < %.2f (p_up=%.3f)", conf, MIN_DEMO_CONFIDENCE, p_up)
        return 0

    side = "up" if p_up >= 0.5 else "down"
    slug = f"btc-updown-5m-{ws}"
    placed = 0
    db: Session = SessionLocal()
    try:
        users = db.execute(
            select(User).join(TradingProfile, TradingProfile.user_id == User.id)
        ).scalars().all()

        for user in users:
            per_user_lock = f"btc_oracle:demo:{ws}:{user.id}"
            if not _r.set(per_user_lock, "1", nx=True, ex=600):
                continue
            profile = user.profile
            if not profile:
                continue
            stake = min(float(getattr(profile, "max_stake_usdc", 100.0)), 100.0)
            sim_price = _sim_ask(p_up, side)
            tokens = round(stake / sim_price, 6)
            # status=filled — reconciler will set real won/lost after window closes
            trade = Trade(
                user_id=user.id,
                window_ts=ws,
                market_slug=slug,
                side=side,
                stake_usdc=stake,
                avg_price=sim_price,
                tokens_filled=tokens,
                is_paper=True,
                status="filled",
                pnl_usdc=0.0,
                order_meta={"demo": True, "p_up": round(p_up, 4)},
            )
            db.add(trade)
            placed += 1
        db.commit()
        if placed:
            _publish({"type": "trade", "window_ts": ws, "side": side, "count": placed})
        log.info("demo trades placed ws=%s side=%s p_up=%.3f count=%d", ws, side, p_up, placed)
    finally:
        db.close()
    return placed


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

    # Real trading: user must have paper_only=False AND a linked wallet.
    # LIVE_TRADING env var acts as a global emergency kill-switch only —
    # individual paper_only toggles control each user independently.
    use_real = not profile.paper_only and bool(user.wallet)
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


# ───────────────────────── Simulated price helper ─────────────────────────

def _sim_ask(p_up: float, side: str) -> float:
    """Simulated fill price — less-informed market assumption.

    Mirrors the backtest_10k.py formula so paper wins yield realistic upside
    rather than $0 profit that happens when real ask≈0.99.
    """
    conf = abs(p_up - 0.5) * 2
    direction = 0.15 if side == "up" else -0.15
    raw = 0.50 + direction * conf + random.gauss(0, 0.03)
    return round(max(0.35, min(0.90, raw)), 3)


# ───────────────────────── Paper demo tick ─────────────────────────

@celery_app.task(name="app.workers.tasks.paper_demo_tick")
def paper_demo_tick() -> dict:
    """Place one simulated paper trade per eligible user every 5 minutes.

    Completely independent of real Polymarket quotes — uses _sim_ask pricing
    so wins yield $40–$115 profit per $100 stake regardless of market extremes.
    Bypasses all risk / edge checks so trades flow continuously.
    """
    ws = current_window_ts()

    # Use cached forecast; fall back to running one inline
    cached = (
        _r.get(f"btc_oracle:pred:{ws + 300}")
        or _r.get(f"btc_oracle:pred:{ws}")
    )
    if cached:
        p_up = json.loads(cached)["p_up"]
    else:
        try:
            fc = asyncio.run(forecast_for_window(ws))
            p_up = fc.p_up
        except Exception as exc:
            log.warning("paper_demo_tick: forecast failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    conf = abs(p_up - 0.5) * 2
    if conf < MIN_DEMO_CONFIDENCE:
        log.info("paper_demo_tick skipped: conf=%.2f < %.2f (p_up=%.3f)", conf, MIN_DEMO_CONFIDENCE, p_up)
        return {"ok": True, "skipped": True, "reason": "low_confidence", "p_up": round(p_up, 4)}

    side = "up" if p_up >= 0.5 else "down"
    slug = f"btc-updown-5m-{ws}"

    placed = 0
    db: Session = SessionLocal()
    try:
        # NOTE: paper_demo_tick runs for ALL users with a profile — it is a
        # demo/simulation task that bypasses auto_trade_enabled intentionally.
        users_with_profile = db.execute(
            select(User).join(TradingProfile, TradingProfile.user_id == User.id)
        ).scalars().all()

        for user in users_with_profile:
            # One demo trade per user per 5-min window (idempotency)
            lock = f"btc_oracle:demo:{ws}:{user.id}"
            if not _r.set(lock, "1", nx=True, ex=600):
                continue

            profile = user.profile
            if not profile:
                continue

            stake = min(float(getattr(profile, "max_stake_usdc", 100.0)), 100.0)
            sim_price = _sim_ask(p_up, side)
            tokens = round(stake / sim_price, 6)

            # status=filled — reconciler resolves to real won/lost after window closes
            trade = Trade(
                user_id=user.id,
                window_ts=ws,
                market_slug=slug,
                side=side,
                stake_usdc=stake,
                avg_price=sim_price,
                tokens_filled=tokens,
                is_paper=True,
                status="filled",
                pnl_usdc=0.0,
                order_meta={"demo": True, "p_up": round(p_up, 4)},
            )
            db.add(trade)
            placed += 1

        db.commit()
    finally:
        db.close()

    log.info("paper_demo_tick ws=%s side=%s p_up=%.3f placed=%d", ws, side, p_up, placed)
    return {"ok": True, "window_ts": ws, "side": side, "p_up": round(p_up, 4), "placed": placed}


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

        now_ts = int(time.time())
        now_dt = datetime.now(timezone.utc)

        # Only process trades whose window has already closed (+ 30s buffer)
        ready = [t for t in open_trades if now_ts >= t.window_ts + 300 + 30]
        if not ready:
            return {"ok": True, "resolved": 0}

        # Fetch klines once for all trades — covers both demo and real.
        # 1-min candles × 720 = 12 hours back (plenty of history).
        from ..ai.market_data import fetch_klines
        klines_ok = False
        df = None
        df_ts = None
        try:
            df = asyncio.run(fetch_klines("1m", 720))
            df_ts = (df["open_time"].astype("int64") // 10**9).values
            klines_ok = True
        except Exception:
            log.warning("klines fetch failed — demo trades will use model probability fallback")

        resolved = 0
        POLY_FEE = 0.02

        for t in ready:
            if not klines_ok:
                # No klines — don't guess. Leave as "filled" and retry next cycle.
                # Never use a random draw; that produces wrong won/lost outcomes.
                continue

            # Determine real BTC direction for this window
            open_idx = next((i for i, v in enumerate(df_ts) if v >= t.window_ts), None)
            close_raw = next((i for i, v in enumerate(df_ts) if v >= t.window_ts + 300), None)
            if open_idx is None or not close_raw or close_raw == 0:
                # Klines don't cover this window (too old). Skip — stays filled.
                continue

            open_p  = float(df["open"].iloc[open_idx])
            close_p = float(df["close"].iloc[close_raw - 1])
            went_up = close_p > open_p
            won = (t.side == "up" and went_up) or (t.side == "down" and not went_up)

            if won:
                t.status = "won"
                t.pnl_usdc = round(t.tokens_filled * (1 - POLY_FEE) - t.stake_usdc, 4)
            else:
                t.status = "lost"
                t.pnl_usdc = round(-t.stake_usdc, 4)
            t.resolved_at = now_dt
            resolved += 1

        db.commit()
        if resolved:
            _publish({"type": "resolved", "count": resolved})
        log.info("reconcile: resolved %d trades", resolved)
    finally:
        db.close()
    return {"ok": True, "resolved": resolved}


@celery_app.task(name="app.workers.tasks.retrain_model")
def retrain_model() -> dict:
    """Retrain the XGBoost direction model on recent real BTC klines.

    Runs every 6 hours via beat. Also callable manually via
    POST /api/ml/retrain?key=btc-oracle-demo-2026.

    Flow:
      1. Fetch 1500 × 1-min klines from Binance (~25 hours of data)
      2. Build features + labels (was price higher 5 min later?)
      3. Train XGBoost, evaluate on 20% holdout
      4. Save model to disk + Redis
      5. Hot-reload in this process so next prediction cycle uses it
      6. Publish SSE event so the Dashboard updates accuracy badge
    """
    from ..ai.market_data import fetch_klines
    from ..ai.engine import _model

    log.info("retrain_model: fetching klines...")
    try:
        klines = asyncio.run(fetch_klines("1m", 1500))
    except Exception as exc:
        log.warning("retrain_model: klines fetch failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    log.info("retrain_model: training on %d candles...", len(klines))
    try:
        meta = _model.train(klines)
    except Exception as exc:
        log.exception("retrain_model: training failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    log.info(
        "retrain_model: done accuracy=%.4f n_train=%d n_val=%d path=%s",
        meta["val_accuracy"], meta["n_train"], meta["n_val"], meta.get("saved_path"),
    )

    _publish({
        "type":         "model_retrained",
        "val_accuracy": meta["val_accuracy"],
        "n_total":      meta["n_total"],
        "trained_at":   meta["trained_at"],
    })

    return {"ok": True, **meta}


@celery_app.task(name="app.workers.tasks.fix_demo_outcomes")
def fix_demo_outcomes() -> dict:
    """One-shot correction: re-check every demo trade (won OR lost) against
    real BTC klines and flip any that have the wrong outcome.

    Trades that were randomly resolved (no klines at reconcile time) may be
    marked 'won' even though BTC went the other way. This task corrects them.
    Only fixes trades whose window falls within the available klines window.
    """
    from ..ai.market_data import fetch_klines

    try:
        df = asyncio.run(fetch_klines("1m", 720))
    except Exception as exc:
        log.warning("fix_demo_outcomes: klines fetch failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    df_ts = (df["open_time"].astype("int64") // 10**9).values
    POLY_FEE = 0.02
    now_dt = datetime.now(timezone.utc)

    db: Session = SessionLocal()
    fixed = 0
    try:
        # Re-check ALL resolved demo trades within the klines window
        demo_trades = db.execute(
            select(Trade).where(
                Trade.is_paper == True,
                Trade.status.in_(["won", "lost", "filled"]),
            )
        ).scalars().all()

        for t in demo_trades:
            meta = t.order_meta or {}
            if not meta.get("demo"):
                continue

            open_idx = next((i for i, v in enumerate(df_ts) if v >= t.window_ts), None)
            close_raw = next((i for i, v in enumerate(df_ts) if v >= t.window_ts + 300), None)
            if open_idx is None or not close_raw or close_raw == 0:
                continue  # outside klines window — can't verify

            open_p  = float(df["open"].iloc[open_idx])
            close_p = float(df["close"].iloc[close_raw - 1])
            went_up = close_p > open_p
            correct_won = (t.side == "up" and went_up) or (t.side == "down" and not went_up)

            correct_status = "won" if correct_won else "lost"
            if t.status != correct_status:
                # Wrong — flip it
                if correct_won:
                    t.status = "won"
                    t.pnl_usdc = round(t.tokens_filled * (1 - POLY_FEE) - t.stake_usdc, 4)
                else:
                    t.status = "lost"
                    t.pnl_usdc = round(-t.stake_usdc, 4)
                t.resolved_at = now_dt
                fixed += 1

        db.commit()
        log.info("fix_demo_outcomes: corrected %d trades", fixed)
    finally:
        db.close()

    if fixed:
        _publish({"type": "resolved", "count": fixed, "source": "fix_demo_outcomes"})
    return {"ok": True, "fixed": fixed}
