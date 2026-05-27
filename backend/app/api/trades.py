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

    import json as _json
    import redis as _redis
    from ..core.config import get_settings as _cfg
    _r = _redis.from_url(_cfg().REDIS_URL, decode_responses=True)
    _r.setex(f"btc_oracle:pred:{ws}", 600, _json.dumps(fc.to_dict()))

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

    # Try the cached forecast for next window, current window, or next-next window
    cached = (r.get(f"btc_oracle:pred:{ws + 300}")
              or r.get(f"btc_oracle:pred:{ws}")
              or r.get(f"btc_oracle:pred:{ws + 600}"))
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

    return {"window_ts": ws, "market_slug": market.slug, "p_up": fc["p_up"], "placed": placed}


@router.post("/admin/force-paper-trade")
async def admin_force_paper_trade(_: User = Depends(get_current_user)):
    """Place a paper trade immediately — bypasses risk/edge checks entirely.
    Useful for verifying the full trade → reconcile flow end-to-end."""
    import asyncio, json, time
    import redis as redis_lib
    from ..core.config import get_settings
    from ..services.polymarket import PolymarketClient, current_window_ts, paper_submit, OrderRequest
    from ..db.session import SessionLocal

    cfg = get_settings()
    ws = current_window_ts()
    poly = PolymarketClient()
    market = await poly.find_btc_market(ws)
    if not market:
        raise HTTPException(status_code=404, detail=f"No Polymarket BTC market for window={ws}")

    # Run a fresh prediction inline
    from ..ai.engine import forecast_for_window
    from ..services.polymarket import next_window_ts
    fc = await forecast_for_window(next_window_ts())

    # Force-pick the side our model prefers, stake $2 regardless of edge
    side = "up" if fc.p_up >= 0.5 else "down"
    ask  = market.up_best_ask if side == "up" else market.down_best_ask
    token_id = market.up_token_id if side == "up" else market.down_token_id
    stake = 2.0

    req = OrderRequest(token_id=token_id, side="BUY", price=ask, size=stake)
    result = await paper_submit(req, ask)

    db: Session = SessionLocal()
    try:
        from ..models import Trade as TradeModel
        trade = TradeModel(
            user_id=_.id if hasattr(_, "id") else 2,
            window_ts=ws,
            market_slug=market.slug,
            side=side,
            stake_usdc=stake,
            avg_price=result.avg_price,
            tokens_filled=result.filled_size / max(result.avg_price, 1e-6),
            is_paper=True,
            status="filled" if result.success else "error",
            order_meta=result.raw,
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        trade_id = trade.id
    finally:
        db.close()

    return {
        "trade_id": trade_id,
        "window_ts": ws,
        "market_slug": market.slug,
        "side": side,
        "p_up": round(fc.p_up, 4),
        "ask": ask,
        "stake": stake,
        "success": result.success,
    }
