from datetime import datetime, timezone

from sqlalchemy import Float, Integer, Boolean, ForeignKey, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base


class TradingProfile(Base):
    __tablename__ = "trading_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    # ─── Master switches ───
    auto_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    live_trading_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    paper_only: Mapped[bool] = mapped_column(Boolean, default=True)

    # ─── Risk dials ───
    # 0 = conservative, 100 = aggressive — drives Kelly multiplier (0..0.5)
    risk_level: Mapped[int] = mapped_column(Integer, default=10)
    max_stake_usdc: Mapped[float] = mapped_column(Float, default=2.0)       # per trade
    daily_loss_limit_usdc: Mapped[float] = mapped_column(Float, default=10.0)
    daily_max_trades: Mapped[int] = mapped_column(Integer, default=20)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.58)      # require P>=this
    max_price: Mapped[float] = mapped_column(Float, default=0.90)           # don't buy above
    side_filter: Mapped[str] = mapped_column(String(8), default="both")     # up|down|both

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship(back_populates="profile")
