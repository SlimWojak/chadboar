"""Chain Status — CLI skill for Flight Recorder health queries.

Usage:
    python3 -m lib.skills.chain_status              # Summary
    python3 -m lib.skills.chain_status --verify      # Full chain verification
    python3 -m lib.skills.chain_status --recent 10   # Last 10 chain beads
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from lib.chain.bead_chain import get_chain_stats, get_chain_tip, verify_chain, _get_conn, ChainBead


def get_summary() -> dict[str, Any]:
    """Get chain health summary."""
    stats = get_chain_stats()
    tip = get_chain_tip()

    result: dict[str, Any] = {
        "status": "OK",
        "chain_length": stats["chain_length"],
        "last_anchor": stats["last_anchor"],
        "beads_since_anchor": stats["beads_since_anchor"],
        "unanchored_beads": stats["unanchored_beads"],
    }

    if tip:
        result["chain_tip"] = {
            "seq": tip.seq,
            "bead_type": tip.bead_type,
            "timestamp": tip.timestamp,
            "bead_hash": tip.bead_hash[:16] + "...",
        }

    # Quick integrity check (last 50 beads only for speed)
    if stats["chain_length"] > 0:
        start = max(1, stats["chain_length"] - 50)
        valid, msg = verify_chain(from_seq=start)
        result["chain_integrity"] = "CLEAN" if valid else "TAMPERED"
        if not valid:
            result["integrity_details"] = msg

    return result


def full_verify() -> dict[str, Any]:
    """Run full chain verification from genesis."""
    valid, msg = verify_chain(from_seq=0)
    stats = get_chain_stats()

    return {
        "status": "OK",
        "chain_integrity": "CLEAN" if valid else "TAMPERED",
        "verification_message": msg,
        "chain_length": stats["chain_length"],
        "last_anchor": stats["last_anchor"],
    }


def get_recent(count: int) -> dict[str, Any]:
    """Get the N most recent chain beads."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT seq, bead_hash, prev_hash, timestamp, bead_type, payload, anchor_tx "
        "FROM chain_beads ORDER BY seq DESC LIMIT ?",
        (count,),
    ).fetchall()
    conn.close()

    beads = []
    for r in rows:
        payload = json.loads(r[5])
        beads.append({
            "seq": r[0],
            "bead_hash": r[1][:16] + "...",
            "bead_type": r[4],
            "timestamp": r[3],
            "payload_summary": _summarize_payload(r[4], payload),
            "anchored": bool(r[6]),
        })

    return {
        "status": "OK",
        "count": len(beads),
        "beads": list(reversed(beads)),  # Chronological order
    }


def _summarize_payload(bead_type: str, payload: dict) -> str:
    """Create a short summary of bead payload."""
    if bead_type == "heartbeat":
        return f"cycle={payload.get('cycle', '?')}, ops={payload.get('opportunities', '?')}"
    elif bead_type in ("trade_entry", "trade_exit"):
        return f"{payload.get('token_symbol', '?')} {payload.get('direction', '?')} {payload.get('amount_sol', '?')} SOL"
    elif bead_type == "anchor":
        return f"tx={payload.get('tx_signature', '?')[:12]}..., beads={payload.get('bead_count', '?')}"
    elif bead_type == "guard_alert":
        return payload.get("alert_type", "?")
    else:
        return json.dumps(payload)[:80]


def main() -> None:
    parser = argparse.ArgumentParser(description="Flight Recorder — Chain Status")
    parser.add_argument("--verify", action="store_true", help="Run full chain verification")
    parser.add_argument("--recent", type=int, metavar="N", help="Show last N chain beads")
    args = parser.parse_args()

    if args.verify:
        result = full_verify()
    elif args.recent:
        result = get_recent(args.recent)
    else:
        result = get_summary()

    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
