"""Drawdown guard — INV-DRAWDOWN-50.

If the current pot value drops below 50% of starting_balance, halt all
trading for 24 hours and alert G.

Usage:
    python3 -m lib.guards.drawdown

Exit codes:
    0 = within tolerance (safe to trade)
    1 = drawdown halt ACTIVE (do not trade)

Output:
    JSON with status, current_pct, and halt details.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from lib.config import load_risk_config
from lib.state import load_state, save_state


def check_drawdown() -> dict:
    """Check if pot has drawn down beyond the halt threshold."""
    state = load_state()
    risk = load_risk_config()

    halt_pct = risk.get("portfolio", {}).get("drawdown_halt_pct", 50)
    halt_hours = risk.get("portfolio", {}).get("drawdown_halt_hours", 24)

    # No starting balance set yet — can't check drawdown
    if state.starting_balance_sol <= 0:
        return {
            "status": "CLEAR",
            "message": "No starting balance configured. Skipping drawdown check.",
            "current_pct": 0.0,
        }

    current_pct = (state.current_balance_sol / state.starting_balance_sol) * 100
    threshold_pct = 100 - halt_pct  # e.g., 50% drawdown means halt at 50% of starting

    # Already halted — check if halt period has expired
    if state.halted and state.halt_reason.startswith("DRAWDOWN"):
        if state.halted_at:
            halted_at = datetime.fromisoformat(state.halted_at)
            now = datetime.now(timezone.utc)
            hours_elapsed = (now - halted_at).total_seconds() / 3600
            if hours_elapsed < halt_hours:
                return {
                    "status": "HALTED",
                    "message": (
                        f"Drawdown halt active. "
                        f"Pot at {current_pct:.1f}% of starting. "
                        f"Halt expires in {halt_hours - hours_elapsed:.1f}h."
                    ),
                    "current_pct": current_pct,
                    "hours_remaining": round(halt_hours - hours_elapsed, 1),
                }
            else:
                # Halt expired — clear it
                state.halted = False
                state.halted_at = ""
                state.halt_reason = ""
                save_state(state)
                return {
                    "status": "CLEAR",
                    "message": f"Drawdown halt expired. Pot at {current_pct:.1f}% of starting. Trading resumed.",
                    "current_pct": current_pct,
                }

    # Check if we should trigger a new halt
    if current_pct <= threshold_pct:
        state.halted = True
        state.halted_at = datetime.now(timezone.utc).isoformat()
        state.halt_reason = f"DRAWDOWN: pot at {current_pct:.1f}% of starting (threshold: {threshold_pct:.0f}%)"
        save_state(state)
        return {
            "status": "HALTED",
            "message": (
                f"DRAWDOWN HALT TRIGGERED. Pot at {current_pct:.1f}% of starting "
                f"(below {threshold_pct:.0f}% threshold). Trading halted for {halt_hours}h."
            ),
            "current_pct": current_pct,
            "hours_remaining": halt_hours,
            "alert": True,
        }

    return {
        "status": "CLEAR",
        "message": f"Pot at {current_pct:.1f}% of starting. Above {threshold_pct:.0f}% threshold.",
        "current_pct": current_pct,
    }


def main() -> None:
    result = check_drawdown()
    print(json.dumps(result, indent=2))
    sys.exit(1 if result["status"] == "HALTED" else 0)


if __name__ == "__main__":
    main()
