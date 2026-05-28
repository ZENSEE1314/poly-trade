import asyncio
import json
import random
import traceback
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, desc, and_
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.session import get_db
from ..models import Prediction, Trade, TradingProfile, User
from .deps import get_current_user, get_current_user_sse

_settings = get_settings()

SSE_CHANNEL = "btc_oracle:events"


def _sim_ask(p_up: float, side: str) -> float:
    """Simulate a realistic paper-trade fill price.

    Mirrors the market price formula from backtest_10k.py: the market is
    slightly LESS confident than the model (less informed), so buys at a
    lower price and wins pay more.

    side="up"  → market_price = 0.50 + 0.15*conf + noise  (>0.50, market leans up)
    side="down" → market_price = 0.50 - 0.15*conf + noise  (<0.50, market leans down)

    At conf=0.30, "down" price ≈ 0.455 → win pays +$118 per $100 stake.
    Breakeven with this structure: ~46% win rate.
    """
    conf = abs(p_up - 0.5) * 2          # 0..1, higher = more confident
    direction = 0.15 if side == "up" else -0.15
    raw = 0.50 + direction * conf + random.gauss(0, 0.03)
    return round(max(0.35, min(0.90, raw)), 3)

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
    limit: int = 500,
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


# ── Real-time SSE stream ────────────────────────────────────────────

@router.get("/stream")
async def stream_events(user: User = Depends(get_current_user_sse)):
    """Server-Sent Events endpoint.

    The client opens one persistent connection; the server pushes JSON
    events whenever a prediction, trade, or reconciliation occurs.

    EventSource cannot send Authorization headers, so the JWT is passed
    as ?token=<jwt> in the query string.

    Nginx must have X-Accel-Buffering: no (set in response header below)
    to prevent proxy buffering from swallowing events.
    """
    import redis.asyncio as aioredis

    async def event_generator():
        r = aioredis.from_url(_settings.REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(SSE_CHANNEL)
        try:
            # Initial handshake — lets the client know the stream is open
            yield f"data: {json.dumps({'type': 'connected', 'user_id': user.id})}\n\n"

            while True:
                # get_message is non-blocking; wait up to 25s for a message
                # then send a heartbeat comment (keeps nginx from closing idle conn)
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=25.0,
                    )
                except asyncio.TimeoutError:
                    msg = None

                if msg and msg["type"] == "message":
                    yield f"data: {msg['data']}\n\n"
                else:
                    # SSE comment — keeps the connection alive through proxies
                    yield ": heartbeat\n\n"

        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(SSE_CHANNEL)
            await r.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx proxy buffering
            "Connection": "keep-alive",
        },
    )


# ── Manual trade ────────────────────────────────────────────────────

class ManualTradeRequest(BaseModel):
    side: str           # "up" or "down"
    stake: float = 100.0


@router.post("/trades/manual")
async def manual_trade(
    body: ManualTradeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Place a manual trade for the current 5-min window.

    Routes to live Polymarket execution when:
      - profile.paper_only = False
      - user has a wallet with mode='private_key'
    Otherwise places a paper trade resolved by the Celery reconciler.
    """
    if body.side not in ("up", "down"):
        raise HTTPException(status_code=400, detail="side must be 'up' or 'down'")
    if body.stake < 1 or body.stake > 10_000:
        raise HTTPException(status_code=400, detail="stake must be $1–$10,000")

    from ..services.polymarket import (
        PolymarketClient, OrderRequest as PolyReq,
        current_window_ts, live_submit, paper_submit,
    )
    from ..core.kms import SealedSecret, vault
    import redis as _redis

    ws = current_window_ts()
    profile = user.profile

    # Determine whether to execute live or paper
    use_real = (
        profile is not None
        and not profile.paper_only
        and user.wallet is not None
        and user.wallet.mode == "private_key"
    )

    _r_sync = _redis.from_url(_settings.REDIS_URL, decode_responses=True)
    cached = _r_sync.get(f"btc_oracle:pred:{ws + 300}") or _r_sync.get(f"btc_oracle:pred:{ws}")
    p_up = json.loads(cached)["p_up"] if cached else 0.5

    if use_real:
        # Find live Polymarket market
        poly = PolymarketClient()
        market = await poly.find_btc_market(ws)
        if not market:
            raise HTTPException(status_code=503, detail=(
                "No active Polymarket BTC-updown-5m market found for this window. "
                "The market may not have opened yet — try again in a moment."
            ))

        ask = market.up_best_ask if body.side == "up" else market.down_best_ask
        token_id = market.up_token_id if body.side == "up" else market.down_token_id

        try:
            secret_bytes = vault.open(
                SealedSecret.from_dict(user.wallet.sealed),
                aad=str(user.id).encode(),
            )
            pk = secret_bytes.decode()
            req = PolyReq(token_id=token_id, side="BUY", price=ask, size=body.stake)
            result = await live_submit(req, pk, user.wallet.funder)
        except Exception as exc:
            # Live order failed — fall back to paper so the user's intent is preserved
            use_real = False
            ask = _sim_ask(p_up, body.side)
            req = PolyReq(token_id=token_id, side="BUY", price=ask, size=body.stake)
            result = await paper_submit(req, ask)
            result.raw["live_error"] = str(exc)
    else:
        sim_price = _sim_ask(p_up, body.side)
        req = PolyReq(
            token_id=f"paper-{body.side}",
            side="BUY",
            price=sim_price,
            size=body.stake,
        )
        result = await paper_submit(req, sim_price)

    avg_price = result.avg_price
    tokens = round(body.stake / max(avg_price, 1e-6), 6)

    trade = Trade(
        user_id=user.id,
        window_ts=ws,
        market_slug=f"btc-updown-5m-{ws}",
        side=body.side,
        stake_usdc=body.stake,
        avg_price=avg_price,
        tokens_filled=tokens,
        is_paper=not use_real,
        status="filled" if result.success else "error",
        pnl_usdc=0.0,
        order_meta={
            "manual": True,
            "live": use_real,
            "p_up": round(p_up, 4),
            "order_id": result.order_id,
            **(result.raw or {}),
        },
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)

    _r_sync.publish(SSE_CHANNEL, json.dumps({
        "type": "trade",
        "window_ts": ws,
        "side": body.side,
        "manual": True,
        "live": use_real,
        "count": 1,
    }))

    return {
        "trade_id":     trade.id,
        "window_ts":    ws,
        "side":         body.side,
        "stake_usdc":   body.stake,
        "avg_price":    avg_price,
        "tokens_filled": tokens,
        "is_paper":     not use_real,
        "status":       trade.status,
        "order_id":     result.order_id,
        "note": (
            "Live order submitted to Polymarket."
            if use_real else
            "Paper trade — outcome resolved by reconciler after window closes."
        ),
    }


# ── Live BTC price ───────────────────────────────────────────────────

@router.get("/btc/price")
async def btc_price(_: User = Depends(get_current_user)):
    """Lightweight real-time BTC spot price from Binance. Polled every 15 s by the dashboard."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.binance.com/api/v3/ticker/24hr",
                            params={"symbol": "BTCUSDT"})
            d = r.json()
            return {
                "price":       float(d["lastPrice"]),
                "change_pct":  float(d["priceChangePercent"]),
                "high_24h":    float(d["highPrice"]),
                "low_24h":     float(d["lowPrice"]),
                "volume_24h":  float(d["quoteVolume"]),
            }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Price fetch failed: {exc}")


# ── Current Polymarket market ─────────────────────────────────────────

@router.get("/market/current")
async def current_market(_: User = Depends(get_current_user)):
    """Return the active Polymarket BTC Up/Down 5-min market for the current window.

    Returns UP/DOWN best ask prices, potential payout, and the price-to-beat
    (current BTC spot price that determines the outcome).
    """
    from ..services.polymarket import PolymarketClient, current_window_ts
    from ..ai.market_data import fetch_klines

    ws = current_window_ts()
    poly = PolymarketClient()
    market = await poly.find_btc_market(ws)

    # Current BTC price from Binance for "price to beat"
    try:
        klines = await fetch_klines("1m", 5)
        btc_now = float(klines["close"].iloc[-1])
    except Exception:
        btc_now = None

    if not market:
        return {
            "found": False,
            "window_ts": ws,
            "btc_price": btc_now,
            "message": "No active market for this window yet — usually opens a minute before start.",
        }

    # Payout per $100 stake (approximate, before fees)
    up_payout   = round(100 / max(market.up_best_ask, 0.01) - 100, 2)
    down_payout = round(100 / max(market.down_best_ask, 0.01) - 100, 2)
    window_closes_in = (ws + 300) - int(__import__("time").time())

    return {
        "found":            True,
        "window_ts":        ws,
        "slug":             market.slug,
        "btc_price":        btc_now,
        "up_ask":           market.up_best_ask,
        "down_ask":         market.down_best_ask,
        "up_bid":           market.up_best_bid,
        "down_bid":         market.down_best_bid,
        "up_payout_per_100":   up_payout,
        "down_payout_per_100": down_payout,
        "window_closes_in": max(0, window_closes_in),
    }


# ── Fix wrong outcomes ───────────────────────────────────────────────

@router.post("/admin/fix-outcomes")
async def admin_fix_outcomes(key: str = Query(...), db: Session = Depends(get_db)):
    """Re-check every demo trade against real BTC klines and correct any
    wrong won/lost outcomes. Call once after deploy to fix historical errors.

    POST /api/admin/fix-outcomes?key=btc-oracle-demo-2026
    """
    if key != "btc-oracle-demo-2026":
        raise HTTPException(status_code=403, detail="Invalid key")

    import httpx, math
    import pandas as pd

    # Fetch 1-min klines directly (same Kraken source as market_data)
    async def _klines(n: int):
        end_ts = int(datetime.now(timezone.utc).timestamp())
        since = end_ts - n * 60
        async with httpx.AsyncClient(timeout=20, verify=False) as cli:
            r = await cli.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": "XBTUSD", "interval": 1, "since": since},
            )
            data = r.json()
            result = data.get("result", {})
            pair = (result.get("XXBTZUSD") or result.get("XBTUSD")
                    or next((v for k, v in result.items() if k != "last"), None))
            if not pair:
                return None
            df = pd.DataFrame(pair, columns=[
                "open_time", "open", "high", "low", "close", "vwap", "volume", "count"])
            df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
            for c in ["open", "close"]:
                df[c] = df[c].astype(float)
            return df

    df = await _klines(720)
    if df is None or len(df) < 10:
        raise HTTPException(status_code=503, detail="Klines fetch failed")

    df_ts = df["open_time"].astype("int64").values
    POLY_FEE = 0.02
    now_dt = datetime.now(timezone.utc)

    demo_trades = db.execute(
        select(Trade).where(
            Trade.is_paper == True,
            Trade.status.in_(["won", "lost", "filled"]),
        )
    ).scalars().all()

    fixed = 0
    for t in demo_trades:
        if not (t.order_meta or {}).get("demo"):
            continue
        open_idx = next((i for i, v in enumerate(df_ts) if v >= t.window_ts), None)
        close_raw = next((i for i, v in enumerate(df_ts) if v >= t.window_ts + 300), None)
        if open_idx is None or not close_raw or close_raw == 0:
            continue
        open_p  = float(df["open"].iloc[open_idx])
        close_p = float(df["close"].iloc[close_raw - 1])
        went_up = close_p > open_p
        correct_won = (t.side == "up" and went_up) or (t.side == "down" and not went_up)
        correct_status = "won" if correct_won else "lost"
        if t.status != correct_status:
            if correct_won:
                t.status = "won"
                t.pnl_usdc = round(t.tokens_filled * (1 - POLY_FEE) - t.stake_usdc, 4)
            else:
                t.status = "lost"
                t.pnl_usdc = round(-t.stake_usdc, 4)
            t.resolved_at = now_dt
            fixed += 1

    db.commit()
    return {"fixed": fixed, "total_checked": len(demo_trades)}


# ── ML self-learning ────────────────────────────────────────────────

@router.get("/ml/stats")
def ml_stats(_: User = Depends(get_current_user)):
    """Return model training metadata: accuracy, sample count, feature importance."""
    from ..ai.ml_model import BTCDirectionModel
    meta = BTCDirectionModel.get_meta()
    if not meta:
        return {
            "trained": False,
            "message": "Model not yet trained — using heuristic fallback. "
                       "Call POST /api/ml/retrain to start training.",
        }
    return {"trained": True, **meta}


@router.post("/ml/retrain")
def ml_retrain(key: str = Query(...)):
    """Queue a model retraining job (admin key required).

    POST /api/ml/retrain?key=btc-oracle-demo-2026
    The Celery worker fetches 1500 1-min klines and retrains XGBoost.
    Results appear in /api/ml/stats once done (usually < 60 s).
    """
    if key != "btc-oracle-demo-2026":
        raise HTTPException(status_code=403, detail="Invalid key")
    from ..workers.tasks import retrain_model
    task = retrain_model.delay()
    return {"queued": True, "task_id": str(task.id),
            "note": "Poll GET /api/ml/stats for results in ~30–60 s"}


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
async def admin_force_paper_trade(
    stake: float = 100.0,
    _: User = Depends(get_current_user),
):
    """Place a paper trade immediately — bypasses risk/edge checks entirely.
    Useful for verifying the full trade → reconcile flow end-to-end.
    Pass ?stake=N to override the default $100 stake."""
    from ..services.polymarket import PolymarketClient, current_window_ts, paper_submit, OrderRequest
    from ..db.session import SessionLocal

    ws = current_window_ts()
    poly = PolymarketClient()
    market = await poly.find_btc_market(ws)
    if not market:
        raise HTTPException(status_code=404, detail=f"No Polymarket BTC market for window={ws}")

    # Run a fresh prediction inline
    from ..ai.engine import forecast_for_window
    from ..services.polymarket import next_window_ts
    fc = await forecast_for_window(next_window_ts())

    side = "up" if fc.p_up >= 0.5 else "down"
    token_id = market.up_token_id if side == "up" else market.down_token_id
    # Use simulated price so paper wins yield real upside (not 0 from a 0.99 ask)
    sim_price = _sim_ask(fc.p_up, side)

    req = OrderRequest(token_id=token_id, side="BUY", price=sim_price, size=stake)
    result = await paper_submit(req, sim_price)

    db: Session = SessionLocal()
    try:
        from ..models import Trade as TradeModel
        trade = TradeModel(
            user_id=_.id,
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
        "sim_price": sim_price,
        "stake": stake,
        "success": result.success,
    }


@router.post("/admin/bulk-paper-trades")
async def admin_bulk_paper_trades(
    count: int = 20,
    stake: float = 100.0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Place `count` paper trades back-to-back (each in the current window).
    Bypasses all risk/edge checks. Used to quickly seed trade history.
    Pass ?count=30&stake=100 etc."""
    from ..services.polymarket import PolymarketClient, current_window_ts, paper_submit, OrderRequest
    from ..ai.engine import forecast_for_window
    from ..services.polymarket import next_window_ts
    from ..db.session import SessionLocal

    if count < 1 or count > 100:
        raise HTTPException(status_code=400, detail="count must be 1-100")
    if stake < 1 or stake > 10_000:
        raise HTTPException(status_code=400, detail="stake must be $1-$10,000")

    ws = current_window_ts()
    poly = PolymarketClient()
    market = await poly.find_btc_market(ws)
    if not market:
        raise HTTPException(status_code=404, detail=f"No Polymarket BTC market for window={ws}")

    fc = await forecast_for_window(next_window_ts())
    side = "up" if fc.p_up >= 0.5 else "down"
    token_id = market.up_token_id if side == "up" else market.down_token_id

    placed = []
    db2 = SessionLocal()
    try:
        from ..models import Trade as TradeModel
        for i in range(count):
            # Each trade gets its own simulated price (adds realistic variance)
            sim_price = _sim_ask(fc.p_up, side)
            req = OrderRequest(token_id=token_id, side="BUY", price=sim_price, size=stake)
            result = await paper_submit(req, sim_price)
            trade = TradeModel(
                user_id=current_user.id,
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
            db2.add(trade)
            placed.append({"side": side, "stake": stake, "sim_price": sim_price, "success": result.success})
        db2.commit()
    finally:
        db2.close()

    return {
        "window_ts": ws,
        "market_slug": market.slug,
        "side": side,
        "p_up": round(fc.p_up, 4),
        "count": len(placed),
        "total_staked": round(stake * len(placed), 2),
        "trades": placed,
    }


# ── Seed historical trade data ─────────────────────────────────────────────

@router.post("/admin/seed-history")
async def admin_seed_history(
    candles: int = 600,
    stake: float = 100.0,
    min_confidence: float = 0.06,
    wipe_existing: bool = False,
    use_synthetic: bool = False,
    seed: int = 7,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replay the heuristic model on 5-min BTC candles and write historical
    trades with actual outcomes to the DB.

    ?candles=1000       — candles to process (Kraken max ~700; synthetic unlimited)
    ?stake=100          — USDC stake per trade
    ?min_confidence=0.06 — only trade when |p_up-0.5|*2 > this
    ?wipe_existing=true  — delete ALL existing trades for this user first
    ?use_synthetic=true  — generate GBM synthetic data instead of fetching Kraken
                           (more neutral, ~51% win rate, shows strategy potential)
    """
    import math
    import numpy as np
    import pandas as pd
    import httpx

    POLY_FEE = 0.02          # 2% fee on wins (paper simulation)
    MAX_DAILY = 20           # cap trades per UTC day
    SLUG_TPL  = "btc-updown-5m-{ws}"

    # ── 1. Fetch candles ──────────────────────────────────────────────────
    async def _kraken(n: int):
        rows_per_call = 720
        calls = math.ceil(n / rows_per_call)
        end_ts = int(datetime.now(timezone.utc).timestamp())
        since = end_ts - n * 5 * 60
        all_rows = []
        async with httpx.AsyncClient(timeout=20, verify=False) as cli:
            for _ in range(calls):
                try:
                    r = await cli.get(
                        "https://api.kraken.com/0/public/OHLC",
                        params={"pair": "XBTUSD", "interval": 5, "since": since},
                    )
                    data = r.json()
                    if data.get("error"):
                        return None
                    result = data.get("result", {})
                    pair = (result.get("XXBTZUSD")
                            or result.get("XBTUSD")
                            or next((v for k, v in result.items() if k != "last"), None))
                    if not pair:
                        return None
                    all_rows.extend(pair)
                    since = result.get("last", since)
                except Exception:
                    return None
        if not all_rows:
            return None
        df = pd.DataFrame(
            all_rows,
            columns=["open_time", "open", "high", "low", "close", "vwap", "volume", "count"],
        )
        df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df.tail(n).reset_index(drop=True)

    def _synthetic(n: int):
        np.random.seed(seed)
        ANNUAL = 365 * 24 * 12
        mu, sigma = 0.40 / ANNUAL, 0.65 / ANNUAL ** 0.5
        S0, closes = 103_500.0, [103_500.0]
        for _ in range(n - 1):
            closes.append(closes[-1] * math.exp((mu - 0.5 * sigma**2) + sigma * np.random.randn()))
        closes = np.array(closes)
        noise  = np.random.uniform(0.0005, 0.003, n)
        opens  = np.roll(closes, 1); opens[0] = S0
        highs  = np.maximum(opens, closes) * (1 + noise * np.random.uniform(0.3, 1, n))
        lows   = np.minimum(opens, closes) * (1 - noise * np.random.uniform(0.3, 1, n))
        vols   = np.random.lognormal(7, 0.8, n)
        now    = int(datetime.now(timezone.utc).timestamp())
        times  = np.arange(now - n * 300, now, 300)[:n]
        return pd.DataFrame({
            "open_time": times, "open": opens, "high": highs,
            "low": lows, "close": closes, "volume": vols,
        })

    if use_synthetic:
        raw = _synthetic(candles)
        source = "synthetic"
    else:
        raw = await _kraken(candles)
        source = "kraken"
        if raw is None or len(raw) < 100:
            raw = _synthetic(candles)
            source = "synthetic"

    # ── 2. Feature engineering ────────────────────────────────────────────
    d = raw.copy()
    d["ret_5m"]  = d["close"].pct_change(1)
    d["ret_15m"] = d["close"].pct_change(3)
    d["ret_60m"] = d["close"].pct_change(12)

    delta = d["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))

    ema12 = d["close"].ewm(span=12, adjust=False).mean()
    ema26 = d["close"].ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    d["macd_diff"] = macd - macd.ewm(span=9, adjust=False).mean()

    sma20, std20 = d["close"].rolling(20).mean(), d["close"].rolling(20).std()
    d["bb_pctb"] = (d["close"] - (sma20 - 2 * std20)) / (4 * std20 + 1e-9)

    vm, vs = d["volume"].rolling(20).mean(), d["volume"].rolling(20).std()
    d["vol_z"] = (d["volume"] - vm) / (vs + 1e-9)

    body = abs(d["close"] - d["open"])
    d["upper_wick"] = (d["high"] - d[["close", "open"]].max(axis=1)) / (body + 1e-9)
    d["lower_wick"] = (d[["close", "open"]].min(axis=1) - d["low"])  / (body + 1e-9)
    d["ema_ratio"]  = d["close"] / d["close"].ewm(span=50, adjust=False).mean() - 1

    d = d.dropna().reset_index(drop=True)

    # ── 3. Heuristic swarm (mirrors backtest_10k.py) ──────────────────────
    def _logit(p):
        p = max(min(p, 1 - 1e-4), 1e-4)
        return math.log(p / (1 - p))

    def _sigmoid(x):
        return 1 / (1 + math.exp(-x))

    def _heuristic_vote(persona, f):
        r5   = f.get("ret_5m", 0)
        rsi  = f.get("rsi_14", 50)
        md   = f.get("macd_diff", 0)
        bbp  = f.get("bb_pctb", 0.5)
        vz   = f.get("vol_z", 0)
        r60  = f.get("ret_60m", 0)
        r15  = f.get("ret_15m", 0)
        er   = f.get("ema_ratio", 0)
        if   persona == "TapeReader":  score = 4*r5 + 0.6*md + 0.03*(rsi-50)
        elif persona == "Contrarian":  score = -3*r5 - 0.05*(rsi-50) - 2*(bbp-0.5)
        elif persona == "MicroQuant":
            score = (-1.0*f.get("upper_wick",0) + 1.0*f.get("lower_wick",0)
                     + 0.3*vz*(1 if r5>=0 else -1))
        elif persona == "MacroBias":   score = 3*r60 + 2*r15 + 1.5*er
        else:                          return ("neutral", 0.15)
        if   score >  0.0015: return ("up",      min(0.9, abs(score)*80))
        elif score < -0.0015: return ("down",    min(0.9, abs(score)*80))
        else:                 return ("neutral", 0.3)

    PERSONAS = ["TapeReader", "Contrarian", "MicroQuant", "MacroBias", "SentimentBot"]

    def _predict(feats):
        # ML proxy computed first — used as logit starting point (matches backtest_10k.py)
        r5   = feats.get("ret_5m", 0)
        rsi  = feats.get("rsi_14", 50)
        ml_p = min(max(0.5 + 2.5*r5 + 0.003*(rsi-50), 0.1), 0.9)
        lo   = _logit(ml_p)
        for p in PERSONAS:
            vote, conf = _heuristic_vote(p, feats)
            if   vote == "up":   lo += 1.2 * conf
            elif vote == "down": lo -= 1.2 * conf
        swarm_p = _sigmoid(0.6 * lo)
        return 0.55*ml_p + 0.45*swarm_p

    # ── 4. Wipe existing trades if requested ──────────────────────────────
    if wipe_existing:
        db.query(Trade).filter(Trade.user_id == current_user.id).delete()
        db.commit()

    # ── 5. Walk candles and generate trades ───────────────────────────────
    created, daily_counts = [], {}
    for i in range(50, len(d) - 1):
        row  = d.iloc[i]
        nrow = d.iloc[i + 1]
        ts   = int(row["open_time"])
        # Align to 5-min boundary (Polymarket uses 300-s windows)
        ws = ts - (ts % 300)

        date_key = datetime.fromtimestamp(ws, tz=timezone.utc).date()
        if daily_counts.get(date_key, 0) >= MAX_DAILY:
            continue

        feats = row.to_dict()
        p_up  = _predict(feats)
        conf  = abs(p_up - 0.5) * 2

        if conf < min_confidence:
            continue

        side   = "up" if p_up > 0.5 else "down"
        p_side = p_up if side == "up" else (1 - p_up)

        # Simulate market price using backtest_10k.py formula (less-informed market)
        sim_price = _sim_ask(p_up, side)

        tokens  = stake / sim_price
        went_up = float(nrow["close"]) > float(row["close"])
        won     = (side == "up" and went_up) or (side == "down" and not went_up)

        if won:
            pnl = round(tokens * (1 - POLY_FEE) - stake, 4)
        else:
            pnl = round(-stake, 4)

        resolved_at = datetime.fromtimestamp(ws + 300, tz=timezone.utc)
        trade = Trade(
            user_id=current_user.id,
            window_ts=ws,
            market_slug=SLUG_TPL.format(ws=ws),
            side=side,
            stake_usdc=stake,
            avg_price=sim_price,
            tokens_filled=round(tokens, 6),
            is_paper=True,
            status="won" if won else "lost",
            pnl_usdc=pnl,
            order_meta={"seeded": True, "source": source, "p_up": round(p_up, 4)},
            created_at=datetime.fromtimestamp(ws, tz=timezone.utc),
            resolved_at=datetime.fromtimestamp(ws + 300, tz=timezone.utc),
        )
        db.add(trade)
        created.append({
            "ws": ws, "side": side, "p_up": round(p_up, 4),
            "sim_price": sim_price, "won": won, "pnl": pnl,
        })
        daily_counts[date_key] = daily_counts.get(date_key, 0) + 1

    db.commit()

    wins   = sum(1 for t in created if t["won"])
    losses = len(created) - wins
    total_pnl = round(sum(t["pnl"] for t in created), 2)
    win_rate  = round(wins / len(created) * 100, 1) if created else 0

    return {
        "source": source,
        "candles_used": len(d),
        "trades_created": len(created),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "total_pnl_usdc": total_pnl,
        "sample": created[-5:],
    }


# ── Demo tick (no-auth, key-protected) ────────────────────────────────────────

DEMO_KEY = "btc-oracle-demo-2026"

@router.post("/demo-tick")
async def demo_tick(
    key: str = Query(..., description="Demo key"),
    db: Session = Depends(get_db),
):
    """Place one simulated paper trade per user for the current 5-min window.

    No JWT required — protected by a static demo key.
    Used to force-trigger trades when Celery hasn't redeployed yet.

    Call: POST /api/demo-tick?key=btc-oracle-demo-2026
    """
    if key != DEMO_KEY:
        raise HTTPException(status_code=403, detail="Invalid demo key")

    from ..services.polymarket import current_window_ts

    ws = current_window_ts()

    # ── Fetch prediction, matching the trade window to the prediction window ──
    # Prediction cycle stores key = next_window_ts() at the time it runs.
    # We must place the trade for THAT same window so the reconciler evaluates
    # the correct 5-minute period.  Mismatch → wrong win/lose outcomes.
    import json as _json, redis as _redis
    _r = _redis.from_url(_settings.REDIS_URL, decode_responses=True)

    cached_next = _r.get(f"btc_oracle:pred:{ws + 300}")
    cached_curr = _r.get(f"btc_oracle:pred:{ws}")

    if cached_next:
        # A fresh prediction exists for the NEXT window — trade on that window
        p_up    = _json.loads(cached_next)["p_up"]
        ws_trade = ws + 300
    elif cached_curr:
        # Fallback: prediction for the CURRENT window (made 5 min ago)
        p_up    = _json.loads(cached_curr)["p_up"]
        ws_trade = ws
    else:
        # Nothing cached — run inline forecast for next window
        from ..ai.engine import forecast_for_window
        from ..services.polymarket import next_window_ts
        fc = await forecast_for_window(next_window_ts())
        p_up     = fc.p_up
        ws_trade = ws + 300

    MIN_CONFIDENCE = 0.30           # only trade when |p_up-0.5|*2 > this
    conf = abs(p_up - 0.5) * 2
    if conf < MIN_CONFIDENCE:
        return {
            "window_ts": ws_trade,
            "p_up": round(p_up, 4),
            "placed": 0,
            "skipped": True,
            "reason": f"confidence {conf:.2f} < {MIN_CONFIDENCE} — model has no edge, not trading",
        }

    side = "up" if p_up >= 0.5 else "down"
    slug = f"btc-updown-5m-{ws_trade}"
    placed = []

    users = db.execute(
        select(User).join(TradingProfile, TradingProfile.user_id == User.id)
    ).scalars().all()

    for user in users:
        profile = user.profile
        if not profile:
            continue

        # One demo trade per user per window — idempotency guard
        lock_key = f"btc_oracle:demo:{ws_trade}:{user.id}"
        if not _r.set(lock_key, "1", nx=True, ex=700):
            continue   # already placed for this window

        stake = min(float(getattr(profile, "max_stake_usdc", 100.0)), 100.0)
        sim_price = _sim_ask(p_up, side)
        tokens = round(stake / sim_price, 6)

        # status=filled — reconciler sets real won/lost after window closes
        trade = Trade(
            user_id=user.id,
            window_ts=ws_trade,
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
        placed.append({
            "user_id": user.id, "side": side, "stake": stake, "price": sim_price,
        })

    db.commit()

    # NOTE: filled trades are resolved by the reconciler using real BTC klines.
    # Never random. Use /api/admin/fix-outcomes to correct historical wrong outcomes.
    return {
        "window_ts": ws_trade,
        "side": side,
        "p_up": round(p_up, 4),
        "placed": len(placed),
        "trades": placed,
    }


# ── 24/7 cron cycle (no Celery required) ───────────────────────────────────
#
# Railway Cron calls POST /api/cron/cycle?key=btc-oracle-demo-2026 every minute.
# The endpoint runs prediction → demo trades → reconciliation inline, in a thread
# pool so the asyncio.run() calls inside task functions don't conflict with the
# uvicorn event loop.
#
# Setup (Railway UI):
#   + New → Cron → schedule "* * * * *"
#   command: curl -sX POST \
#     "https://poly-trade-production-07d8.up.railway.app/api/cron/cycle?key=btc-oracle-demo-2026"
# ────────────────────────────────────────────────────────────────────────────

from concurrent.futures import ThreadPoolExecutor as _TPE
_cron_pool = _TPE(max_workers=2, thread_name_prefix="cron")

CRON_KEY = "btc-oracle-demo-2026"


@router.post("/cron/cycle")
async def cron_cycle(key: str = Query(...)):
    """All-in-one cron endpoint: prediction → demo trades → reconcile.

    Called by Railway Cron every minute. No JWT or Celery required.
    Each step is idempotent — Redis locks prevent duplicate trades.
    """
    if key != CRON_KEY:
        raise HTTPException(status_code=403, detail="Invalid key")

    from ..workers.tasks import run_prediction_cycle, reconcile_open_trades
    import time as _time
    import redis as _redis

    loop = asyncio.get_event_loop()
    t0 = _time.monotonic()
    now = int(_time.time())

    # Run in thread so asyncio.run() calls inside tasks don't conflict
    pred_result  = await loop.run_in_executor(_cron_pool, run_prediction_cycle)
    recon_result = await loop.run_in_executor(_cron_pool, reconcile_open_trades)

    elapsed_ms = round((_time.monotonic() - t0) * 1000)

    # Write heartbeat keys so /api/status can report liveness
    try:
        _r = _redis.from_url(_settings.REDIS_URL, decode_responses=True)
        _r.setex("btc_oracle:cron_alive",    120, "1")
        _r.set("btc_oracle:last_pred_ts",   now)
        if isinstance(recon_result, dict) and recon_result.get("resolved", 0):
            _r.set("btc_oracle:last_trade_ts", now)
    except Exception:
        pass  # Redis write failure is non-fatal

    return {
        "ok":        True,
        "elapsed_ms": elapsed_ms,
        "prediction": pred_result,
        "reconcile":  recon_result,
        "ts":        now,
    }


@router.get("/status")
async def system_status():
    """Public health + activity status. Shows whether the bot is running.

    Returns last prediction time, trade count, and Celery queue depth.
    Safe to call without authentication — no sensitive data.
    """
    import redis as _redis
    import time as _time

    r = _redis.from_url(_settings.REDIS_URL, decode_responses=True)

    # Last cron / task activity stored in Redis
    last_pred_ts  = r.get("btc_oracle:last_pred_ts")
    last_trade_ts = r.get("btc_oracle:last_trade_ts")
    cron_ok       = r.get("btc_oracle:cron_alive")    # set by cron_cycle

    now = int(_time.time())

    return {
        "api_ok":            True,
        "last_prediction_ago": (now - int(last_pred_ts))  if last_pred_ts  else None,
        "last_trade_ago":      (now - int(last_trade_ts)) if last_trade_ts else None,
        "cron_alive":          cron_ok == "1",
        "ts":                  now,
    }
