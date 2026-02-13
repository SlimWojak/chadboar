"""Risk guard — INV-DAILY-EXPOSURE-30 + circuit breakers.

Checks daily exposure limits, consecutive loss circuit breakers,
and position count limits before allowing new trades.

Usage:
    python3 -m lib.guards.risk

Exit codes:
    0 = within limits (safe to trade)
    1 = limit reached (do not open new positions)

Output:
    JSON with status and limit details.
"""

from __future__ import annotations

import json
import sys

from lib.config import load_risk_config
from lib.state import check_daily_reset, load_state, save_state


def check_risk() -> dict:
    """Check all risk limits. Returns structured status."""
    state = load_state()
    state = check_daily_reset(state)
    save_state(state)

    risk = load_risk_config()
    portfolio = risk.get("portfolio", {})
    circuit = risk.get("circuit_breakers", {})

    issues: list[str] = []
    warnings: list[str] = []

    # Daily exposure check (INV-DAILY-EXPOSURE-30)
    max_daily_pct = portfolio.get("daily_exposure_pct", 30)
    if state.current_balance_sol > 0:
        daily_pct = (state.daily_exposure_sol / state.current_balance_sol) * 100
        if daily_pct >= max_daily_pct:
            issues.append(
                f"Daily exposure at {daily_pct:.1f}% (limit: {max_daily_pct}%). "
                "No new entries until tomorrow."
            )
    else:
        daily_pct = 0.0

    # Position count check
    max_positions = portfolio.get("max_concurrent_positions", 5)
    if len(state.positions) >= max_positions:
        issues.append(
            f"Max positions reached ({len(state.positions)}/{max_positions}). "
            "Close a position before opening a new one."
        )

    # Consecutive losses circuit breaker
    max_consec = circuit.get("consecutive_losses", 3)
    if state.consecutive_losses >= max_consec:
        warnings.append(
            f"Consecutive losses: {state.consecutive_losses} (threshold: {max_consec}). "
            "Reduce position size by 50%."
        )

    # Daily loss circuit breaker
    max_daily_loss = circuit.get("daily_loss_pct", 10)
    if state.daily_loss_pct >= max_daily_loss:
        issues.append(
            f"Daily loss at {state.daily_loss_pct:.1f}% (limit: {max_daily_loss}%). "
            "Trading halted for rest of day."
        )

    status = "BLOCKED" if issues else ("WARNING" if warnings else "CLEAR")

    return {
        "status": status,
        "daily_exposure_pct": round(daily_pct, 1),
        "open_positions": len(state.positions),
        "max_positions": max_positions,
        "consecutive_losses": state.consecutive_losses,
        "issues": issues,
        "warnings": warnings,
        "message": "; ".join(issues + warnings) if (issues or warnings) else "All risk limits clear.",
    }


def main() -> None:
    result = check_risk()
    print(json.dumps(result, indent=2))
    sys.exit(1 if result["status"] == "BLOCKED" else 0)


if __name__ == "__main__":
    main()
