from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, desc, and_
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..models import Prediction, Trade, User
from .deps import get_current_user

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/predictions/latest")
def latest_predictions(limit: int = 20, db: Session = Depends(get_db)):
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
