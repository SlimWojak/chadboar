"""Edge Bank — Bead Write CLI entry point.

Writes a trade autopsy bead to beads/ and edge.db.

Usage:
    python3 -m lib.skills.bead_write --type entry --data '{"token_symbol": "BOAR", ...}'
"""

from __future__ import annotations

import argparse
import json
import sys

from lib.edge.bank import Bead, EdgeBank


def write_bead(bead_type: str, data: dict) -> dict:
    """Write a bead and return confirmation."""
    bank = EdgeBank()

    bead = Bead(
        bead_type=bead_type,
        token_mint=data.get("token_mint", ""),
        token_symbol=data.get("token_symbol", ""),
        direction=data.get("direction", ""),
        amount_sol=float(data.get("amount_sol", 0)),
        price_usd=float(data.get("price_usd", 0)),
        thesis=data.get("thesis", ""),
        signals=data.get("signals", []),
        outcome=data.get("outcome", "pending"),
        pnl_pct=float(data.get("pnl_pct", 0)),
        exit_reason=data.get("exit_reason", ""),
        market_conditions=data.get("market_conditions", ""),
    )

    bead_id = bank.write_bead(bead)
    stats = bank.get_stats()

    return {
        "status": "OK",
        "bead_id": bead_id,
        "message": f"Bead written: {bead_id}",
        "bank_stats": stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Edge Bank — Write Bead")
    parser.add_argument("--type", required=True, choices=["entry", "exit"], dest="bead_type")
    parser.add_argument("--data", required=True, help="JSON string with bead data")
    args = parser.parse_args()

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "ERROR", "error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    result = write_bead(args.bead_type, data)
    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
