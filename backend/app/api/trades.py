import traceback
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc, and_
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..models import Prediction, Trade, User
from .deps import get_current_user

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/predictions/latest")
def latest_predictions(
    limit: int = 20,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(select(Prediction).order_by(desc(Prediction.id)).limit(limit))
        .scalars()
        .all()
    )
    return [
        {
            "window_ts": r.window_ts,
            "p_up": r.p_up,
            "ml_p_up": r.ml_p_up,
            "swarm_p_up": r.swarm_p_up,
            "btc_price": r.btc_price,
            "votes": r.swarm_votes,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/trades/mine")
def my_trades(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(Trade).where(Trade.user_id == user.id).order_by(desc(Trade.id)).limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": t.id,
            "window_ts": t.window_ts,
            "side": t.side,
            "stake_usdc": t.stake_usdc,
            "avg_price": t.avg_price,
            "is_paper": t.is_paper,
            "status": t.status,
            "pnl_usdc": t.pnl_usdc,
            "created_at": t.created_at.isoformat(),
        }
        for t in rows
    ]


@router.get("/stats/mine")
def my_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    rows = (
        db.execute(
            select(Trade).where(and_(Trade.user_id == user.id, Trade.created_at >= since))
        )
        .scalars()
        .all()
    )
    resolved = [t for t in rows if t.status in ("won", "lost")]
    wins = sum(1 for t in resolved if t.status == "won")
    pnl = sum(t.pnl_usdc for t in resolved)
    return {
        "trades_7d": len(rows),
        "resolved_7d": len(resolved),
        "win_rate": (wins / len(resolved)) if resolved else None,
        "pnl_usdc_7d": pnl,
    }


# ── Admin / diagnostics ─────────────────────────────────────────────

@router.post("/admin/run-prediction")
async def admin_run_prediction(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run one full prediction cycle inline (bypasses Celery). Use to verify the engine works."""
    from ..ai.engine import forecast_for_window
    from ..services.polymarket import next_window_ts

    ws = next_window_ts()
    try:
        fc = await forecast_for_window(ws)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "trace": traceback.format_exc()})

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
    return fc.to_dict()


@router.post("/admin/inject-test-trade")
async def admin_inject_test_trade(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a paper trade for the most-recently-closed 5-min window.
    Bypasses prediction, decide(), and Polymarket entirely — use this to
    verify the reconcile flow works before worrying about edge/confidence."""
    import time
    from ..services.polymarket import WINDOW_SECS

    now = int(time.time())
    # Two full windows back guarantees close_ts is always > 30s in the past,
    # regardless of where we are in the current window.
    ws = (now - (now % WINDOW_SECS)) - 2 * WINDOW_SECS

    trade = Trade(
        user_id=user.id,
        window_ts=ws,
        market_slug=f"bitcoin-up-or-down-{ws}",
        side="up",
        stake_usdc=1.0,
        avg_price=0.50,
        tokens_filled=2.0,   # 1 USDC / 0.50 = 2 tokens
        is_paper=True,
        status="filled",
        order_meta={"paper": True, "injected": True},
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return {
        "trade_id": trade.id,
        "window_ts": ws,
        "window_closed_at": ws + WINDOW_SECS,
        "message": "Test trade injected — call POST /api/admin/run-reconcile to resolve it.",
    }


@router.post("/admin/run-reconcile")
async def admin_run_reconcile(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Resolve all filled trades inline (bypasses Celery). Call this after the window closes."""
    import asyncio, time
    from datetime import datetime, timezone
    import pandas as pd
    from sqlalchemy import select as sa_select
    from ..ai.market_data import fetch_klines

    open_trades = db.execute(
        sa_select(Trade).where(Trade.status == "filled")
    ).scalars().all()

    if not open_trades:
        return {"ok": True, "message": "No filled trades to reconcile", "resolved": []}

    try:
        klines = await fetch_klines("1m", 1440)  # 24 hours of 1-min candles
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Klines fetch failed: {exc}")

    df = klines
    # Robustly convert timezone-aware datetimes to Unix seconds
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    df_ts = ((df["open_time"] - epoch) / pd.Timedelta("1s")).astype("int64").values

    now = int(time.time())
    resolved = []
    skipped = []

    for t in open_trades:
        close_ts = t.window_ts + 300
        if now < close_ts + 30:
            skipped.append({"trade_id": t.id, "reason": "window not closed yet", "closes_in": close_ts + 30 - now})
            continue

        open_idx = next((i for i, v in enumerate(df_ts) if v >= t.window_ts), None)
        close_raw = next((i for i, v in enumerate(df_ts) if v >= close_ts), None)
        if open_idx is None:
            skipped.append({"trade_id": t.id, "reason": "window_ts not in klines range", "window_ts": t.window_ts, "klines_start": int(df_ts[0])})
            continue
        if close_raw is None or close_raw == 0:
            skipped.append({"trade_id": t.id, "reason": "close_ts not in klines range", "close_ts": close_ts, "klines_end": int(df_ts[-1])})
            continue

        close_idx = close_raw - 1
        open_p = float(df["open"].iloc[open_idx])
        close_p = float(df["close"].iloc[close_idx])
        went_up = close_p > open_p
        won = (t.side == "up" and went_up) or (t.side == "down" and not went_up)

        t.status = "won" if won else "lost"
        t.pnl_usdc = round(t.tokens_filled - t.stake_usdc, 4) if won else round(-t.stake_usdc, 4)
        t.resolved_at = datetime.now(timezone.utc)
        resolved.append({
            "trade_id": t.id, "side": t.side, "status": t.status,
            "open_price": open_p, "close_price": close_p,
            "pnl_usdc": t.pnl_usdc,
        })

    db.commit()
    return {"ok": True, "resolved": resolved, "skipped": skipped}


@router.post("/admin/run-trade-tick")
async def admin_run_trade_tick(_: User = Depends(get_current_user)):
    """Manually fire a trade tick (ignores timing guard). Use after run-prediction."""
    import asyncio, json, time
    import redis as redis_lib
    from ..core.config import get_settings
    from ..services.polymarket import PolymarketClient, current_window_ts, paper_submit, OrderRequest
    from ..services.risk import decide
    from ..models import TradingProfile
    from ..db.session import SessionLocal

    cfg = get_settings()
    r = redis_lib.from_url(cfg.REDIS_URL, decode_responses=True)
    ws = current_window_ts()

    # Try the cached forecast for either this window or next
    cached = r.get(f"btc_oracle:pred:{ws + 300}") or r.get(f"btc_oracle:pred:{ws}")
    if not cached:
        raise HTTPException(status_code=404, detail="No forecast in Redis cache. Run /admin/run-prediction first.")

    fc = json.loads(cached)
    poly = PolymarketClient()
    market = await poly.find_btc_market(ws)
    if not market:
        raise HTTPException(status_code=404, detail=f"No Polymarket BTC market found for window={ws}")

    db: Session = SessionLocal()
    placed = []
    try:
        from sqlalchemy import select as sa_select
        users = db.execute(
            sa_select(User)
            .join(TradingProfile, TradingProfile.user_id == User.id)
            .where(TradingProfile.auto_trade_enabled == True)
        ).scalars().all()

        for user in users:
            profile = user.profile
            if not profile:
                continue
            decision = decide(db, profile, fc["p_up"], market.up_best_ask, market.down_best_ask)
            if not decision.should_trade:
                placed.append({"user_id": user.id, "skipped": True, "reason": decision.reason})
                continue

            token_id = market.up_token_id if decision.side == "up" else market.down_token_id
            ask = market.up_best_ask if decision.side == "up" else market.down_best_ask
            req = OrderRequest(token_id=token_id, side="BUY", price=ask, size=decision.stake_usdc)
            result = await paper_submit(req, ask)

            trade = Trade(
                user_id=user.id,
                window_ts=ws,
                market_slug=market.slug,
                side=decision.side,
                stake_usdc=decision.stake_usdc,
                avg_price=result.avg_price,
                tokens_filled=result.filled_size / max(result.avg_price, 1e-6),
                is_paper=True,
                status="filled" if result.success else "error",
                order_meta=result.raw,
            )
            db.add(trade)
            placed.append({"user_id": user.id, "side": decision.side, "stake": decision.stake_usdc, "price": result.avg_price})

        db.commit()
    finally:
        db.close()

    trades_created = sum(1 for p in placed if "skipped" not in p)
    hint = None
    if trades_created == 0 and placed:
        reasons = list({p["reason"] for p in placed if p.get("skipped")})
        hint = f"All trades skipped ({'; '.join(reasons)}). Use POST /api/admin/inject-test-trade to bypass decide() and test the reconcile flow."

    return {"window_ts": ws, "market_slug": market.slug, "p_up": fc["p_up"], "placed": placed, "hint": hint}
