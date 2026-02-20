"""ECDSA signing for bead attestation.

Node signing key (secp256r1) proves "this node produced this bead."
Different from signer.key (which signs Solana transactions and is root-owned).
The node signing key is autistboar-owned — if an attacker has process access,
they can already write arbitrary beads, so root isolation adds no security.

Key paths:
  Private: /etc/autistboar/node_signing.key (chmod 400, autistboar:autistboar)
  Public:  state/node_signing.pub (readable by all)

For a8ra: this becomes per-Gate signing with HSM-backed keys.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from ecdsa import NIST256p, SigningKey, VerifyingKey, BadSignatureError

PRIVATE_KEY_PATH = Path("/etc/autistboar/node_signing.key")
WORKSPACE = Path(__file__).resolve().parent.parent.parent
PUBLIC_KEY_PATH = WORKSPACE / "state" / "node_signing.pub"

_cached_signing_key: SigningKey | None = None
_cached_verifying_key: VerifyingKey | None = None
_cached_code_hash: str | None = None


def _generate_keypair() -> tuple[SigningKey, VerifyingKey]:
    """Generate a new ECDSA secp256r1 keypair."""
    sk = SigningKey.generate(curve=NIST256p)
    vk = sk.get_verifying_key()
    return sk, vk


def _save_private_key(sk: SigningKey) -> None:
    """Save private key to /etc/autistboar/node_signing.key.

    Attempts chmod 400 + chown. If chown fails (not root), the key is still
    saved with restrictive permissions — acceptable for single-user VPS.
    """
    PRIVATE_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY_PATH.write_bytes(sk.to_pem())
    os.chmod(PRIVATE_KEY_PATH, 0o400)
    try:
        subprocess.run(
            ["chown", "autistboar:autistboar", str(PRIVATE_KEY_PATH)],
            check=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _save_public_key(vk: VerifyingKey) -> None:
    """Save public key to state/node_signing.pub (world-readable)."""
    PUBLIC_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_KEY_PATH.write_bytes(vk.to_pem())


def _load_or_create_keys() -> tuple[SigningKey, VerifyingKey]:
    """Load existing keypair or generate new one on first run."""
    if PRIVATE_KEY_PATH.exists():
        pem = PRIVATE_KEY_PATH.read_bytes()
        sk = SigningKey.from_pem(pem)
        vk = sk.get_verifying_key()
        if not PUBLIC_KEY_PATH.exists():
            _save_public_key(vk)
        return sk, vk

    sk, vk = _generate_keypair()
    _save_private_key(sk)
    _save_public_key(vk)
    return sk, vk


def get_signing_key() -> SigningKey:
    """Get the cached signing key, loading/creating on first call."""
    global _cached_signing_key, _cached_verifying_key
    if _cached_signing_key is None:
        _cached_signing_key, _cached_verifying_key = _load_or_create_keys()
    return _cached_signing_key


def get_verifying_key() -> VerifyingKey:
    """Get the cached verifying key, loading/creating on first call."""
    global _cached_signing_key, _cached_verifying_key
    if _cached_verifying_key is None:
        _cached_signing_key, _cached_verifying_key = _load_or_create_keys()
    return _cached_verifying_key


def get_public_key_hex() -> str:
    """Get the public key as hex string (for attestation envelopes)."""
    vk = get_verifying_key()
    return vk.to_string().hex()


def sign_hash(hash_hex: str) -> str:
    """Sign a SHA-256 hash (hex string) and return signature as hex.

    Uses deterministic RFC 6979 signing (default for python-ecdsa).
    """
    sk = get_signing_key()
    hash_bytes = bytes.fromhex(hash_hex)
    sig = sk.sign_digest(hash_bytes, hashfunc=hashlib.sha256)
    return sig.hex()


def verify_signature(hash_hex: str, sig_hex: str, pub_key_hex: str | None = None) -> bool:
    """Verify an ECDSA signature against a hash.

    Uses the node's public key by default. Pass pub_key_hex to verify
    against a different key (e.g., from a remote node in a8ra).
    """
    try:
        if pub_key_hex:
            vk = VerifyingKey.from_string(
                bytes.fromhex(pub_key_hex), curve=NIST256p,
            )
        else:
            vk = get_verifying_key()

        hash_bytes = bytes.fromhex(hash_hex)
        sig_bytes = bytes.fromhex(sig_hex)
        return vk.verify_digest(sig_bytes, hash_bytes)
    except (BadSignatureError, ValueError, Exception):
        return False


def get_code_hash() -> str:
    """Get current git commit hash. Cached after first call."""
    global _cached_code_hash
    if _cached_code_hash is not None:
        return _cached_code_hash

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(WORKSPACE),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            _cached_code_hash = result.stdout.strip()
        else:
            _cached_code_hash = "unknown"
    except Exception:
        _cached_code_hash = "unknown"

    return _cached_code_hash


NODE_ID = "chadboar-vps-sg1"
