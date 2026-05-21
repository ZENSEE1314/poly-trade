import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.kms import vault
from ..db.session import get_db
from ..models import User, Wallet
from ..schemas.wallet import WalletApiKeyIn, WalletPrivateKeyIn, WalletOut
from .deps import get_current_user

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
    import os

    if os.getenv("ALLOW_PK_MODE", "false").lower() != "true":
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


@router.delete("", status_code=204)
def unlink_wallet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.wallet:
        db.delete(user.wallet)
        db.commit()
    return None
