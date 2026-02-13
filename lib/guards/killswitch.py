"""Killswitch guard — INV-KILLSWITCH.

If killswitch.txt exists in the workspace root, the system must halt immediately.
No trades, no skill execution, no state updates.

Usage:
    python3 -m lib.guards.killswitch

Exit codes:
    0 = no killswitch (safe to proceed)
    1 = killswitch ACTIVE (halt immediately)

Output:
    JSON with status and message.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
KILLSWITCH_FILE = WORKSPACE / "killswitch.txt"


def check_killswitch() -> dict:
    """Check if killswitch.txt exists."""
    if KILLSWITCH_FILE.exists():
        content = KILLSWITCH_FILE.read_text().strip()
        return {
            "status": "ACTIVE",
            "message": f"Killswitch is ACTIVE. Reason: {content or 'No reason given'}",
            "file": str(KILLSWITCH_FILE),
        }
    return {
        "status": "CLEAR",
        "message": "No killswitch. Safe to proceed.",
    }


def main() -> None:
    result = check_killswitch()
    print(json.dumps(result, indent=2))
    sys.exit(1 if result["status"] == "ACTIVE" else 0)


if __name__ == "__main__":
    main()
