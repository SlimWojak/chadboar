"""Edge Bank — Bead Query CLI entry point.

Queries similar historical patterns for pre-trade pattern recognition.

Usage:
    python3 -m lib.skills.bead_query --context "whale accumulation, 5x volume, AI narrative"
"""

from __future__ import annotations

import argparse
import json
import sys

from lib.edge.bank import EdgeBank


def query_beads(context: str, top_k: int = 3) -> dict:
    """Query similar beads and return matches."""
    bank = EdgeBank()
    matches = bank.query_similar(context, top_k=top_k)
    stats = bank.get_stats()

    return {
        "status": "OK",
        "matches": matches,
        "match_count": len(matches),
        "total_beads": stats["total_beads"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Edge Bank — Query Similar Patterns")
    parser.add_argument("--context", required=True, help="Signal context to match against")
    parser.add_argument("--top-k", type=int, default=3, help="Number of matches (default: 3)")
    args = parser.parse_args()

    result = query_beads(args.context, args.top_k)
    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
