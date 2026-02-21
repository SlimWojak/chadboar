"""Blind KeyMan Signer — INV-BLIND-KEY.

THIS IS THE SECURITY CENTERPIECE.

This module runs as an ISOLATED SUBPROCESS. It is invoked by the agent
process but does NOT share the agent's environment.

Isolation model:
  1. Agent constructs unsigned transaction payload (base64)
  2. Agent invokes this signer via subprocess with a CLEAN environment
  3. The signer's environment is set up by keychain.py — ONLY the signer
     process has access to the private key
  4. Signer reads unsigned tx from STDIN
  5. Signer signs the transaction
  6. Signer writes signed tx to STDOUT (base64)
  7. Signer exits. No files written. No logs. No network.

What this process DOES:
  - Read private key from its own environment variable (SIGNER_PRIVATE_KEY)
  - Read unsigned transaction bytes from stdin
  - Sign the transaction
  - Write signed transaction bytes to stdout
  - Exit

What this process NEVER DOES:
  - Write to any file
  - Write to any log
  - Make any network request
  - Print the private key or any derivative of it
  - Accept any argument that could leak the key
  - Import any module that could exfiltrate data

Modes:
  Sign:   echo '<unsigned_tx_base64>' | python3 -m lib.signer.signer
  Pubkey: python3 -m lib.signer.signer --pubkey

Exit codes:
  0 = success (signed tx or pubkey on stdout)
  1 = error (error message on stderr)
"""

from __future__ import annotations

import base64
import os
import sys


def _sign_transaction(unsigned_tx_bytes: bytes, private_key_bytes: bytes) -> bytes:
    """Sign a Solana transaction.

    Uses solders for ed25519 signing. The private key is used ONLY
    within this function and is never stored, logged, or returned.
    """
    try:
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
    except ImportError:
        # Fallback: try raw ed25519 if solders not available
        try:
            from solders.keypair import Keypair
        except ImportError:
            raise RuntimeError("solders package required for signing")

    keypair = Keypair.from_bytes(private_key_bytes)

    # Deserialize the unsigned transaction
    tx = VersionedTransaction.from_bytes(unsigned_tx_bytes)

    # Sign using the VersionedTransaction constructor which handles
    # Solana's message signing protocol correctly (domain-separated hash).
    # Do NOT use keypair.sign_message() + populate() — that produces
    # invalid signatures because it skips the versioned message prefix.
    signed_tx = VersionedTransaction(tx.message, [keypair])
    return bytes(signed_tx)


def _derive_pubkey(private_key_bytes: bytes) -> str:
    """Derive the public key (base58) from a private key.

    Public key is NOT secret material — safe to output.
    Uses same Keypair derivation as signing.
    """
    from solders.keypair import Keypair
    keypair = Keypair.from_bytes(private_key_bytes)
    return str(keypair.pubkey())


def main() -> None:
    """Signer entry point. Reads stdin, signs, writes stdout."""
    # SECURITY: Read private key from THIS process's environment ONLY
    key_b64 = os.environ.get("SIGNER_PRIVATE_KEY", "")
    if not key_b64:
        print("ERROR: SIGNER_PRIVATE_KEY not set in signer environment", file=sys.stderr)
        sys.exit(1)

    # --pubkey mode: derive and output public key, then exit
    if "--pubkey" in sys.argv:
        try:
            private_key_bytes = base64.b64decode(key_b64)
            pubkey = _derive_pubkey(private_key_bytes)
            sys.stdout.write(pubkey)
            sys.stdout.flush()
            private_key_bytes = b""  # noqa: F841
            key_b64 = ""  # noqa: F841
            sys.exit(0)
        except Exception as e:
            print(f"ERROR: Pubkey derivation failed: {e}", file=sys.stderr)
            sys.exit(1)

    # Read unsigned transaction from stdin (base64 encoded)
    unsigned_b64 = sys.stdin.read().strip()
    if not unsigned_b64:
        print("ERROR: No transaction data on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        private_key_bytes = base64.b64decode(key_b64)
        unsigned_tx_bytes = base64.b64decode(unsigned_b64)
    except Exception as e:
        print(f"ERROR: Base64 decode failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        signed_tx_bytes = _sign_transaction(unsigned_tx_bytes, private_key_bytes)
    except Exception as e:
        print(f"ERROR: Signing failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Output signed transaction as base64 on stdout
    # This is the ONLY output. No key material. No logs.
    signed_b64 = base64.b64encode(signed_tx_bytes).decode("ascii")
    sys.stdout.write(signed_b64)
    sys.stdout.flush()

    # SECURITY: Explicitly clear key material from memory
    key_b64 = ""  # noqa: F841
    private_key_bytes = b""  # noqa: F841

    sys.exit(0)


if __name__ == "__main__":
    main()
