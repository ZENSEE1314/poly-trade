import json
import logging

import httpx
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

    log.info("Forwarding EIP-712 ClobAuth signature to Polymarket: address=%s", checksummed)

    # Forward the EIP-712 signature to Polymarket as L1 auth headers.
    # Polymarket verifies using the same ClobAuth struct + domain as py-clob-client,
    # so their verifier is authoritative. No need for a local re-verification that
    # uses a different Python EIP-712 implementation and produces wrong hashes.
    #
    # POLY_ADDRESS must match the address in the signed ClobAuth.address field.
    # Frontend sends lowercase (MetaMask default) so we forward lowercase here.
    # Polymarket reconstructs the struct using POLY_ADDRESS — it must be consistent.
    poly_headers = {
        "POLY_ADDRESS": checksummed,
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
