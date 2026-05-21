from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..models import TradingProfile, User
from ..schemas.profile import ProfileOut, ProfileUpdate
from .deps import get_current_user

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _to_out(p: TradingProfile) -> ProfileOut:
    return ProfileOut(
        auto_trade_enabled=p.auto_trade_enabled,
        paper_only=p.paper_only,
        risk_level=p.risk_level,
        max_stake_usdc=p.max_stake_usdc,
        daily_loss_limit_usdc=p.daily_loss_limit_usdc,
        daily_max_trades=p.daily_max_trades,
        min_confidence=p.min_confidence,
        max_price=p.max_price,
        side_filter=p.side_filter,
    )


@router.get("", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user)):
    return _to_out(user.profile)


@router.patch("", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = user.profile
    data = payload.model_dump(exclude_unset=True)

    # Special-case the live-trading switch: requires wallet AND acknowledgment
    wants_live = data.get("paper_only") is False
    if wants_live:
        if not user.wallet or not user.wallet.is_active:
            raise HTTPException(400, "Link a Polymarket wallet before enabling live trading")
        if not (p.live_trading_acknowledged or data.get("live_trading_acknowledged")):
            raise HTTPException(400, "Must acknowledge live-trading risk first")

    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _to_out(p)
