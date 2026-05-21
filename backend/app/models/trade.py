from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, Float, Integer, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base


class Prediction(Base):
    """One forecast for a single 5-min BTC window."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    window_ts: Mapped[int] = mapped_column(Integer, index=True)  # unix start
    p_up: Mapped[float] = mapped_column(Float)
    ml_p_up: Mapped[float] = mapped_column(Float)
    swarm_p_up: Mapped[float] = mapped_column(Float)
    swarm_votes: Mapped[dict] = mapped_column(JSON)
    btc_price: Mapped[float] = mapped_column(Float)
    features: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    prediction_id: Mapped[int | None] = mapped_column(ForeignKey("predictions.id"), nullable=True)
    window_ts: Mapped[int] = mapped_column(Integer, index=True)
    market_slug: Mapped[str] = mapped_column(String(128))
    side: Mapped[str] = mapped_column(String(4))      # "up" | "down"
    stake_usdc: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)
    tokens_filled: Mapped[float] = mapped_column(Float, default=0.0)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="submitted")  # submitted|filled|won|lost|cancelled|error
    pnl_usdc: Mapped[float] = mapped_column(Float, default=0.0)
    order_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="trades")
