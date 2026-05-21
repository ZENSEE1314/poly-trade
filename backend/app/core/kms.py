"""Envelope encryption for user-supplied secrets (private keys, API creds).

Each secret is encrypted with a freshly-generated 256-bit Data Encryption Key
(DEK). The DEK is itself encrypted with the Master Key (which in production
lives in a real KMS like AWS KMS, GCP KMS or HashiCorp Vault). We store only:

    {ciphertext, nonce, wrapped_dek, wrapped_dek_nonce, version}

so compromise of the database alone is insufficient to recover plaintext.

This local implementation uses AES-256-GCM with a master key read from env.
Swap `MasterKeyProvider` for a KMS-backed implementation in production.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings


class MasterKeyProvider:
    """Abstract — replace with KMS in prod."""

    def get_key(self) -> bytes:
        raise NotImplementedError


class EnvMasterKey(MasterKeyProvider):
    def get_key(self) -> bytes:
        cfg = get_settings()
        b64 = cfg.MASTER_KMS_KEY_B64
        if not b64 or b64.startswith("changeme"):
            if cfg.APP_ENV == "production":
                raise RuntimeError(
                    "MASTER_KMS_KEY_B64 must be set in production. "
                    "Generate one with: python -c \"import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
                )
            # Ephemeral key for dev only — rotates on every restart.
            return AESGCM.generate_key(bit_length=256)
        return base64.b64decode(b64)


@dataclass
class SealedSecret:
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    wrapped_dek_nonce: bytes
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "wrapped_dek": base64.b64encode(self.wrapped_dek).decode(),
            "wrapped_dek_nonce": base64.b64encode(self.wrapped_dek_nonce).decode(),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SealedSecret":
        return cls(
            ciphertext=base64.b64decode(d["ciphertext"]),
            nonce=base64.b64decode(d["nonce"]),
            wrapped_dek=base64.b64decode(d["wrapped_dek"]),
            wrapped_dek_nonce=base64.b64decode(d["wrapped_dek_nonce"]),
            version=d.get("version", 1),
        )


class Vault:
    def __init__(self, mk_provider: MasterKeyProvider | None = None):
        self._mk = (mk_provider or EnvMasterKey()).get_key()

    def seal(self, plaintext: str | bytes, aad: bytes = b"") -> SealedSecret:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ct = AESGCM(dek).encrypt(nonce, plaintext, aad)
        dek_nonce = os.urandom(12)
        wrapped = AESGCM(self._mk).encrypt(dek_nonce, dek, aad)
        return SealedSecret(ct, nonce, wrapped, dek_nonce)

    def open(self, sealed: SealedSecret, aad: bytes = b"") -> bytes:
        dek = AESGCM(self._mk).decrypt(sealed.wrapped_dek_nonce, sealed.wrapped_dek, aad)
        return AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, aad)


vault = Vault()
