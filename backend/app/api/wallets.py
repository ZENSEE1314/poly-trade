import json
import logging

import httpx
from eth_account import Account
from eth_account.messages import encode_structured_data
from eth_utils import to_checksum_address
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from ..core.config import get_settings
from ..core.kms import vault
from ..db.session import get_db
from ..models import User, Wallet
from ..schemas.wallet import MetaMaskConnectIn, WalletApiKeyIn, WalletPrivateKeyIn, WalletOut
from .deps import get_current_user

CLOB_HOST = "https://clob.polymarket.com"

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


@router.get("", response_model=WalletOut | None)
def get_wallet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = user.wallet
    if not w:
        return None
    return WalletOut(address=w.address, mode=w.mode, funder=w.funder, is_active=w.is_active)


@router.post("/api-key", response_model=WalletOut)
def link_via_api_key(
    payload: WalletApiKeyIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """RECOMMENDED. Stores Polymarket L2 API credentials only."""
    secret = json.dumps(
        {
            "api_key": payload.api_key,
            "api_secret": payload.api_secret,
            "api_passphrase": payload.api_passphrase,
        }
    )
    sealed = vault.seal(secret, aad=str(user.id).encode())
    if user.wallet:
        db.delete(user.wallet)
        db.flush()
    w = Wallet(
        user_id=user.id,
        address=payload.address,
        mode="api_key",
        sealed=sealed.to_dict(),
        funder=payload.funder,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return WalletOut(address=w.address, mode=w.mode, funder=w.funder, is_active=w.is_active)


@router.post("/private-key", response_model=WalletOut)
def link_via_private_key(
    payload: WalletPrivateKeyIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HIGH RISK. Encrypts EOA private key under envelope encryption.
    Disabled at deployment time via env flag in production."""
    if not get_settings().ALLOW_PK_MODE:
        raise HTTPException(403, "Private-key mode is disabled on this deployment")
    if not payload.ack_risk:
        raise HTTPException(400, "Must acknowledge risk")

    sealed = vault.seal(payload.private_key, aad=str(user.id).encode())
    if user.wallet:
        db.delete(user.wallet)
        db.flush()
    w = Wallet(
        user_id=user.id,
        address=payload.address,
        mode="private_key",
        sealed=sealed.to_dict(),
        funder=payload.funder,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return WalletOut(address=w.address, mode=w.mode, funder=w.funder, is_active=w.is_active)


@router.post("/connect-metamask", response_model=WalletOut)
async def connect_via_metamask(
    payload: MetaMaskConnectIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One-click MetaMask wallet link via EIP-712 structured data signing.

    The frontend calls eth_signTypedData_v4 with ClobAuth struct (Polymarket's
    EIP-712 domain). We forward the signature as Polymarket L1 auth headers to
    POST /auth/api-key, which returns L2 API credentials without us ever seeing
    the user's private key.
    """
    try:
        checksummed = to_checksum_address(payload.address)
    except ValueError:
        raise HTTPException(400, detail="Invalid Ethereum address")

    # Verify the EIP-712 signature locally before forwarding to Polymarket.
    # Must match the exact structure used by py-clob-client's sign_clob_auth_message().
    clob_auth_typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name",    "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "ClobAuth": [
                {"name": "address",   "type": "string"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce",     "type": "uint256"},
                {"name": "message",   "type": "string"},
            ],
        },
        "primaryType": "ClobAuth",
        "domain": {
            "name": "ClobAuthDomain",
            "version": "1",
            "chainId": 137,
        },
        "message": {
            "address":   payload.address,
            "timestamp": str(payload.timestamp),
            "nonce":     payload.nonce,
            "message":   "This message attests that I control the given wallet",
        },
    }
    try:
        encoded = encode_structured_data(primitive=clob_auth_typed_data)
        recovered = Account.recover_message(encoded, signature=payload.signature)
    except Exception as exc:
        log.error("EIP-712 signature parse error: %s", exc)
        raise HTTPException(
            400,
            detail=f"Cannot parse EIP-712 signature: {exc}",
        )

    if recovered.lower() != payload.address.lower():
        log.error("Sig mismatch: recovered=%s expected=%s", recovered, payload.address)
        raise HTTPException(
            400,
            detail=f"Signature mismatch: recovered {recovered}, expected {payload.address}",
        )

    log.info("EIP-712 signature OK: address=%s", checksummed)

    # POLY_ADDRESS must match the address used in the signed ClobAuth.address field.
    # Frontend signs with lowercase (MetaMask default) so forward lowercase here.
    # We store checksummed in our DB but Polymarket must verify with the same value.
    # py-clob-client uses prepend_zx() which adds 0x — send signature with 0x as-is.
    poly_headers = {
        "POLY_ADDRESS": payload.address,
        "POLY_SIGNATURE": payload.signature,
        "POLY_TIMESTAMP": str(payload.timestamp),
        "POLY_NONCE": str(payload.nonce),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{CLOB_HOST}/auth/api-key", headers=poly_headers)

    log.info("Polymarket /auth/api-key → %s: %s", r.status_code, r.text[:300])

    if r.status_code != 200:
        # "Invalid L1 Request headers" from Polymarket usually means the wallet
        # has never been registered on polymarket.com. The user must visit
        # polymarket.com, connect their MetaMask wallet and accept ToS first.
        raise HTTPException(
            400,
            detail=(
                f"Polymarket rejected the request: {r.text}. "
                "If your wallet has never been used on Polymarket before, please visit "
                "polymarket.com, connect your MetaMask wallet there first, then retry."
            ),
        )

    data = r.json()
    # Polymarket returns {apiKey, secret, passphrase}
    api_key = data.get("apiKey") or data.get("api_key")
    api_secret = data.get("secret") or data.get("api_secret")
    api_passphrase = data.get("passphrase") or data.get("api_passphrase")

    if not all([api_key, api_secret, api_passphrase]):
        raise HTTPException(500, detail="Polymarket returned incomplete credentials")

    secret_json = json.dumps({
        "api_key": api_key,
        "api_secret": api_secret,
        "api_passphrase": api_passphrase,
    })
    sealed = vault.seal(secret_json, aad=str(user.id).encode())

    if user.wallet:
        db.delete(user.wallet)
        db.flush()

    w = Wallet(
        user_id=user.id,
        address=checksummed,
        mode="api_key",
        sealed=sealed.to_dict(),
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return WalletOut(address=w.address, mode=w.mode, funder=w.funder, is_active=w.is_active)


@router.delete("", status_code=204)
def unlink_wallet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.wallet:
        db.delete(user.wallet)
        db.commit()
    return None
