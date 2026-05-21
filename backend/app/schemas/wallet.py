from pydantic import BaseModel, Field


class WalletApiKeyIn(BaseModel):
    """Recommended mode: user creates Polymarket L2 API creds themselves
    (Settings → API Keys on Polymarket) and pastes them here.

    We never touch the EOA private key in this mode — orders are signed with
    the API secret which can ONLY trade, not withdraw."""

    address: str = Field(min_length=10)
    funder: str | None = None  # proxy funder address (optional)
    api_key: str
    api_secret: str
    api_passphrase: str


class WalletPrivateKeyIn(BaseModel):
    """HIGH-RISK mode. Disabled in production deployments by default."""

    address: str = Field(min_length=10)
    funder: str | None = None
    private_key: str = Field(min_length=64, max_length=66)
    ack_risk: bool


class MetaMaskConnectIn(BaseModel):
    """One-click wallet link via MetaMask.
    Frontend signs "{timestamp}{nonce}" with personal_sign; backend exchanges
    that L1 signature for Polymarket L2 API credentials server-side."""

    address: str
    signature: str   # 0x-prefixed hex from MetaMask personal_sign
    timestamp: int   # Unix seconds used in the signed message
    nonce: int = 0   # Always 0 for first key creation


class WalletOut(BaseModel):
    address: str
    mode: str
    funder: str | None
    is_active: bool
