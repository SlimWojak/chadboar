"""Boot verification — check chain integrity on startup.

Called during heartbeat boot sequence (step 1c). Verifies local hash chain
integrity and compares against last on-chain anchor.

Behavior on tamper detection: Alert only, do NOT halt.
G can manually halt via killswitch if warranted.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from lib.chain.bead_chain import get_chain_stats, verify_chain
from lib.chain.merkle import compute_merkle_root

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DB_PATH = WORKSPACE / "edge.db"


def verify_on_boot(db_path: Path | None = None) -> dict[str, Any]:
    """Verify chain integrity on boot.

    Steps:
    1. Verify local hash chain from last anchor forward (not full chain)
    2. If anchor exists, recompute Merkle root for anchored range
    3. Compare against stored anchor root
    4. Return status dict

    Performance: Only verifies from last anchor forward on normal boot.
    Full chain verification available via chain_status --verify.
    """
    path = db_path or DB_PATH

    stats = get_chain_stats(path)

    if stats["chain_length"] == 0:
        return {"status": "CLEAN", "chain_length": 0, "last_anchor_seq": None}

    # Determine verification start point
    last_anchor = stats.get("last_anchor")
    if last_anchor and last_anchor.get("seq"):
        from_seq = last_anchor["seq"]
    else:
        from_seq = 0

    # Verify hash chain from last anchor forward
    valid, msg = verify_chain(from_seq=from_seq, db_path=path)

    if not valid:
        return {
            "status": "TAMPERED",
            "details": msg,
            "chain_length": stats["chain_length"],
            "last_anchor_seq": last_anchor["seq"] if last_anchor else None,
        }

    # If we have an anchor, verify the Merkle root matches
    if last_anchor and last_anchor.get("merkle_root"):
        import sqlite3
        conn = sqlite3.connect(path)

        # Get the anchor bead's payload for the seq range
        anchor_row = conn.execute(
            "SELECT payload FROM chain_beads WHERE seq = ?",
            (last_anchor["seq"],),
        ).fetchone()

        if anchor_row:
            anchor_payload = json.loads(anchor_row[0])
            seq_range = anchor_payload.get("seq_range", [])

            if len(seq_range) == 2:
                # Recompute Merkle root for the anchored range
                rows = conn.execute(
                    "SELECT bead_hash FROM chain_beads WHERE seq >= ? AND seq <= ? ORDER BY seq ASC",
                    (seq_range[0], seq_range[1]),
                ).fetchall()
                hashes = [r[0] for r in rows]
                recomputed_root = compute_merkle_root(hashes)

                stored_root = anchor_payload.get("merkle_root", "")
                if recomputed_root != stored_root:
                    conn.close()
                    return {
                        "status": "TAMPERED",
                        "details": (
                            f"Merkle root mismatch at anchor seq {last_anchor['seq']}: "
                            f"stored={stored_root[:16]}... recomputed={recomputed_root[:16]}..."
                        ),
                        "chain_length": stats["chain_length"],
                        "last_anchor_seq": last_anchor["seq"],
                    }

        conn.close()

    if not last_anchor:
        return {
            "status": "UNANCHORED",
            "chain_length": stats["chain_length"],
            "last_anchor_seq": None,
        }

    return {
        "status": "CLEAN",
        "chain_length": stats["chain_length"],
        "last_anchor_seq": last_anchor["seq"],
        "beads_since_anchor": stats["beads_since_anchor"],
    }


async def send_tamper_alert(details: str) -> None:
    """Send tamper detection alert to G via Telegram."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    if not token or not channel_id:
        print(f"[chain] TAMPER ALERT (no Telegram): {details}", file=sys.stderr)
        return

    text = (
        f"🔴 CRITICAL: CHAIN TAMPERED\n\n"
        f"Flight recorder detected integrity violation:\n"
        f"{details}\n\n"
        f"Local bead chain has been modified. Possible causes:\n"
        f"- Database file manually edited\n"
        f"- Disk corruption\n"
        f"- Unauthorized access\n\n"
        f"Action: Review edge.db chain_beads table. "
        f"Use killswitch.txt to halt if warranted."
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": channel_id, "text": text},
            )
    except Exception:
        print(f"[chain] TAMPER ALERT (Telegram failed): {details}", file=sys.stderr)
