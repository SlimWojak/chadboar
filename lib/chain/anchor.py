"""Solana on-chain anchoring — memo transactions for bead chain integrity.

Anchors a Merkle root of bead hashes to Solana via the SPL Memo program.
Uses Blind KeyMan for signing (same subprocess isolation model).
Uses Helius RPC for blockhash retrieval and transaction submission.

Cost: ~0.000005 SOL per memo tx (~$0.0004 at $78/SOL).
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from lib.chain.merkle import compute_merkle_root
from lib.signer.keychain import SignerError, get_public_key, sign_transaction

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# SPL Memo program ID
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"

# Cached wallet pubkey (derived once, reused)
_cached_pubkey: str | None = None


def get_wallet_pubkey() -> str:
    """Get the wallet public key, caching after first derivation.

    Calls signer subprocess in --pubkey mode on first call.
    Public key is NOT secret material.
    """
    global _cached_pubkey
    if _cached_pubkey is None:
        _cached_pubkey = get_public_key()
    return _cached_pubkey


def _get_helius_rpc_url() -> str:
    """Get Helius RPC URL from environment."""
    api_key = os.environ.get("HELIUS_API_KEY", "")
    if api_key:
        return f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    return "https://api.mainnet-beta.solana.com"


async def _rpc_call(method: str, params: list[Any] | None = None) -> dict[str, Any]:
    """Make a JSON-RPC call to Solana."""
    url = _get_helius_rpc_url()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


def build_memo_transaction(
    memo_data: str,
    wallet_pubkey: str,
    recent_blockhash: str,
) -> str:
    """Build an unsigned memo transaction as base64.

    Uses solders to construct a VersionedTransaction with a single
    Memo instruction containing the anchor payload.
    """
    from solders.hash import Hash
    from solders.instruction import Instruction
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction

    payer = Pubkey.from_string(wallet_pubkey)
    memo_program = Pubkey.from_string(MEMO_PROGRAM_ID)

    # Memo instruction: data is the UTF-8 encoded memo string
    # The signer's pubkey must be in the accounts list for the memo program
    memo_ix = Instruction(
        memo_program,
        memo_data.encode("utf-8"),
        [],  # Memo program doesn't require account metas for basic memos
    )

    blockhash = Hash.from_string(recent_blockhash)
    msg = MessageV0.try_compile(payer, [memo_ix], [], blockhash)

    # Create unsigned transaction (no signatures yet)
    tx = VersionedTransaction(msg, [])
    tx_bytes = bytes(tx)
    return base64.b64encode(tx_bytes).decode("ascii")


async def submit_anchor(
    merkle_root: str,
    seq_start: int,
    seq_end: int,
    bead_count: int,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Full anchor flow: compute Merkle root, build tx, sign, submit.

    If merkle_root is empty, computes it from the bead range.

    Returns:
        {"status": "OK", "tx_signature": "...", "merkle_root": "...", "cost_lamports": N}
        or {"status": "ERROR", "error": "..."}
    """
    try:
        # Compute Merkle root if not provided
        if not merkle_root:
            from lib.chain.bead_chain import _get_conn
            conn = _get_conn(db_path)
            rows = conn.execute(
                "SELECT bead_hash FROM chain_beads WHERE seq >= ? AND seq <= ? ORDER BY seq ASC",
                (seq_start, seq_end),
            ).fetchall()
            conn.close()
            hashes = [r[0] for r in rows]
            merkle_root = compute_merkle_root(hashes)

        # Build memo payload
        now = datetime.now(timezone.utc).isoformat()
        memo_payload = json.dumps({
            "v": 1,
            "type": "boar_anchor",
            "root": merkle_root,
            "range": [seq_start, seq_end],
            "n": bead_count,
            "ts": now,
        }, separators=(",", ":"))

        # Get wallet pubkey
        wallet_pubkey = get_wallet_pubkey()

        # Get recent blockhash
        blockhash_resp = await _rpc_call("getLatestBlockhash", [{"commitment": "finalized"}])
        if "error" in blockhash_resp:
            return {"status": "ERROR", "error": f"Blockhash fetch failed: {blockhash_resp['error']}"}
        recent_blockhash = blockhash_resp["result"]["value"]["blockhash"]

        # Build unsigned transaction
        unsigned_tx_b64 = build_memo_transaction(memo_payload, wallet_pubkey, recent_blockhash)

        # Sign via Blind KeyMan
        signed_tx_b64 = sign_transaction(unsigned_tx_b64)

        # Submit transaction
        send_resp = await _rpc_call("sendTransaction", [
            signed_tx_b64,
            {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"},
        ])

        if "error" in send_resp:
            return {"status": "ERROR", "error": f"Transaction send failed: {send_resp['error']}"}

        tx_signature = send_resp["result"]

        return {
            "status": "OK",
            "tx_signature": tx_signature,
            "merkle_root": merkle_root,
            "seq_range": [seq_start, seq_end],
            "bead_count": bead_count,
            "cost_lamports": 5000,  # Base fee for memo tx
        }

    except SignerError as e:
        return {"status": "ERROR", "error": f"Signer error: {e}"}
    except httpx.HTTPError as e:
        return {"status": "ERROR", "error": f"RPC error: {e}"}
    except Exception as e:
        print(f"[anchor] Unexpected error: {e}", file=sys.stderr)
        return {"status": "ERROR", "error": str(e)}
