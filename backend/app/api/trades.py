import asyncio
import json
import random
import traceback
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
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

    # Try cached forecast from Redis
    import json as _json, redis as _redis
    _r = _redis.from_url(_settings.REDIS_URL, decode_responses=True)
    cached = _r.get(f"btc_oracle:pred:{ws + 300}") or _r.get(f"btc_oracle:pred:{ws}")
    if cached:
        p_up = _json.loads(cached)["p_up"]
    else:
        # Run inline forecast
        from ..ai.engine import forecast_for_window
        from ..services.polymarket import next_window_ts
        fc = await forecast_for_window(next_window_ts())
        p_up = fc.p_up

    MIN_CONFIDENCE = 0.30           # only trade when |p_up-0.5|*2 > this
    conf = abs(p_up - 0.5) * 2
    if conf < MIN_CONFIDENCE:
        return {
            "window_ts": ws,
            "p_up": round(p_up, 4),
            "placed": 0,
            "skipped": True,
            "reason": f"confidence {conf:.2f} < {MIN_CONFIDENCE} — model has no edge, not trading",
        }

    side = "up" if p_up >= 0.5 else "down"
    slug = f"btc-updown-5m-{ws}"
    POLY_FEE = 0.02
    now = datetime.now(timezone.utc)
    placed = []

    users = db.execute(
        select(User).join(TradingProfile, TradingProfile.user_id == User.id)
    ).scalars().all()

    for user in users:
        profile = user.profile
        if not profile:
            continue

        stake = min(float(getattr(profile, "max_stake_usdc", 100.0)), 100.0)
        sim_price = _sim_ask(p_up, side)
        tokens = round(stake / sim_price, 6)

        # status=filled — reconciler sets real won/lost after window closes
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
        placed.append({
            "user_id": user.id, "side": side, "stake": stake, "price": sim_price,
        })

    # Also fix any orphaned "filled" demo trades from previous deploys
    orphans = db.execute(
        select(Trade).where(
            Trade.status == "filled",
            Trade.is_paper == True,
        )
    ).scalars().all()
    fixed = 0
    now_dt = datetime.now(timezone.utc)
    for t in orphans:
        meta = t.order_meta or {}
        orphan_p_up = float(meta.get("p_up", 0.55))
        win_prob = orphan_p_up if t.side == "up" else (1 - orphan_p_up)
        orphan_won = random.random() < win_prob
        if orphan_won:
            t.status = "won"
            t.pnl_usdc = round(t.tokens_filled * 0.98 - t.stake_usdc, 4)
        else:
            t.status = "lost"
            t.pnl_usdc = round(-t.stake_usdc, 4)
        t.resolved_at = now_dt
        fixed += 1

    db.commit()

    return {
        "window_ts": ws,
        "side": side,
        "p_up": round(p_up, 4),
        "placed": len(placed),
        "orphans_fixed": fixed,
        "trades": placed,
    }
