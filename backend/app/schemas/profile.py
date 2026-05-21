from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    auto_trade_enabled: bool
    paper_only: bool
    live_trading_acknowledged: bool
    risk_level: int
    max_stake_usdc: float
    daily_loss_limit_usdc: float
    daily_max_trades: int
    min_confidence: float
    max_price: float
    side_filter: str


class ProfileUpdate(BaseModel):
    auto_trade_enabled: bool | None = None
    paper_only: bool | None = None
    live_trading_acknowledged: bool | None = None
    risk_level: int | None = Field(default=None, ge=0, le=100)
    max_stake_usdc: float | None = Field(default=None, ge=1, le=1000)
    daily_loss_limit_usdc: float | None = Field(default=None, ge=1, le=1000)
    daily_max_trades: int | None = Field(default=None, ge=1, le=288)
    min_confidence: float | None = Field(default=None, ge=0.50, le=0.95)
    max_price: float | None = Field(default=None, ge=0.50, le=0.99)
    side_filter: str | None = Field(default=None, pattern="^(up|down|both)$")
