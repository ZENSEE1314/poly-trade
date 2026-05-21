from app.core.kms import Vault, SealedSecret


def test_seal_open_roundtrip():
    v = Vault()
    secret = "0x" + "a" * 64
    sealed = v.seal(secret, aad=b"user:42")
    blob = sealed.to_dict()
    restored = SealedSecret.from_dict(blob)
    assert v.open(restored, aad=b"user:42").decode() == secret


def test_aad_mismatch_fails():
    import pytest
    from cryptography.exceptions import InvalidTag

    v = Vault()
    sealed = v.seal("hi", aad=b"user:1")
    with pytest.raises(InvalidTag):
        v.open(sealed, aad=b"user:2")
