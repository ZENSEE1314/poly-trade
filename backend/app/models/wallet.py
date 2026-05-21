from datetime import datetime, timezone

from sqlalchemy import String, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.session import Base


class Wallet(Base):
    """A user's Polymarket connection.

    Two supported modes:
      • mode='api_key' (RECOMMENDED): stores Polymarket L2 API credentials
        only. The user keeps their EOA private key. We can place orders but
        cannot move funds.
      • mode='private_key' (HIGH RISK, opt-in): stores the encrypted EOA
        private key. Disabled in production deployments by default.
    """

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    address: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="api_key")
    # JSON-serialized SealedSecret (see core/kms.py). Contents depend on `mode`.
    sealed: Mapped[dict] = mapped_column(JSON)
    funder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="wallet")
