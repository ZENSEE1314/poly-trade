import json

import httpx
from eth_utils import to_checksum_address
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
    """One-click MetaMask wallet link.

    The frontend asks MetaMask to sign "{timestamp}{nonce}" via personal_sign,
    then POSTs the result here. We forward it as Polymarket L1 auth headers to
    POST /auth/api-key, which returns L2 API credentials (key/secret/passphrase)
    without us ever seeing the user's private key.
    """
    # MetaMask returns lowercase addresses; Polymarket requires EIP-55 checksum format.
    try:
        checksummed = to_checksum_address(payload.address)
    except ValueError:
        raise HTTPException(400, detail="Invalid Ethereum address")

    poly_headers = {
        "POLY_ADDRESS": checksummed,
        "POLY_SIGNATURE": payload.signature,
        "POLY_TIMESTAMP": str(payload.timestamp),
        "POLY_NONCE": str(payload.nonce),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{CLOB_HOST}/auth/api-key",
            headers=poly_headers,
            # NOTE: some Polymarket endpoints gate on Content-Type even for header-only auth
            json={},
        )

    if r.status_code != 200:
        raise HTTPException(400, detail=f"Polymarket rejected the signature: {r.text}")

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
