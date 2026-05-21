"""Risk / position sizing. Server-authoritative — every order goes through here."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import Trade, TradingProfile

settings = get_settings()


@dataclass
class TradeDecision:
    should_trade: bool
    side: str | None
    stake_usdc: float
    max_price: float
    reason: str


def kelly_fraction(p: float, price: float) -> float:
    """Kelly for a binary bet that pays $1 if you win, costs `price` if you lose.

    Edge = p - price. b = (1 - price) / price.
    f* = (p*b - (1-p)) / b
    """
    if price <= 0 or price >= 1:
        return 0.0
    b = (1 - price) / price
    f = (p * b - (1 - p)) / b
    return max(0.0, f)


def daily_usage(db: Session, user_id: int) -> tuple[float, int]:
    """Returns (loss_in_usdc, trade_count) over the last 24h."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = db.execute(
        select(Trade).where(and_(Trade.user_id == user_id, Trade.created_at >= since))
    ).scalars().all()
    loss = sum(max(0.0, -t.pnl_usdc) for t in rows if t.status in ("won", "lost"))
    return loss, len(rows)


def decide(
    db: Session,
    profile: TradingProfile,
    p_up: float,
    up_ask: float,
    down_ask: float,
) -> TradeDecision:
    if not profile.auto_trade_enabled:
        return TradeDecision(False, None, 0, 0, "auto_trade disabled")

    p_down = 1 - p_up
    up_edge = p_up - up_ask
    down_edge = p_down - down_ask

    if up_edge >= down_edge:
        side, p, price = "up", p_up, up_ask
        edge = up_edge
    else:
        side, p, price = "down", p_down, down_ask
        edge = down_edge

    if profile.side_filter != "both" and profile.side_filter != side:
        return TradeDecision(False, None, 0, 0, f"side {side} blocked by filter")

    if p < profile.min_confidence:
        return TradeDecision(False, None, 0, 0, f"confidence {p:.2f}<{profile.min_confidence}")
    if edge < settings.MIN_EDGE:
        return TradeDecision(False, None, 0, 0, f"edge {edge:.3f}<min_edge")
    if price > profile.max_price:
        return TradeDecision(False, None, 0, 0, f"ask {price:.2f}>max_price")

    loss_24h, n_24h = daily_usage(db, profile.user_id)
    if loss_24h >= profile.daily_loss_limit_usdc:
        return TradeDecision(False, None, 0, 0, "daily loss limit reached")
    if n_24h >= profile.daily_max_trades:
        return TradeDecision(False, None, 0, 0, "daily trade count reached")

    # Kelly-capped stake
    kelly = kelly_fraction(p, price)
    aggressiveness = max(0.02, min(0.5, profile.risk_level / 200.0))  # 0..0.5
    bank = max(1.0, profile.daily_loss_limit_usdc - loss_24h)
    stake = min(profile.max_stake_usdc, bank * aggressiveness * kelly * 4)
    # ALSO enforce a hard global cap regardless of user setting
    stake = min(stake, settings.GLOBAL_MAX_DAILY_USDC - loss_24h)

    if stake < 1.0:
        return TradeDecision(False, None, 0, 0, f"stake too small ({stake:.2f})")

    return TradeDecision(True, side, round(stake, 2), price, f"edge={edge:.3f} kelly={kelly:.2f}")
