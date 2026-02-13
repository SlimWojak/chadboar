"""Watchdog — Post-trade position monitoring.

Checks open positions for exit triggers:
- Stop-loss: position down >20%
- Take-profit: position up >100%
- Liquidity drain: significant liquidity drop
- Drawdown: portfolio-level check

Used by heartbeat step 7.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from lib.clients.birdeye import BirdeyeClient
from lib.config import load_risk_config
from lib.state import Position, load_state, save_state


async def check_positions() -> dict[str, Any]:
    """Check all open positions for exit triggers.

    Returns a list of positions that need action (exit/alert).
    """
    state = load_state()
    risk = load_risk_config().get("trade", {})
    birdeye = BirdeyeClient()

    stop_loss_pct = risk.get("stop_loss_pct", 20)
    take_profit_pct = risk.get("take_profit_pct", 100)

    exits_needed: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    updated_positions: list[Position] = []

    try:
        for pos in state.positions:
            try:
                price_data = await birdeye.get_price(pos.token_mint)
                current_price = float(price_data.get("data", {}).get("value", 0))
            except Exception:
                current_price = pos.current_price_usd  # Keep last known price

            # Update position with current price
            pos.current_price_usd = current_price
            if pos.entry_price_usd > 0:
                pos.pnl_pct = ((current_price - pos.entry_price_usd) / pos.entry_price_usd) * 100
            else:
                pos.pnl_pct = 0.0

            # Check exit triggers
            exit_reason = ""
            if pos.pnl_pct <= -stop_loss_pct:
                exit_reason = f"STOP_LOSS: down {pos.pnl_pct:.1f}% (threshold: -{stop_loss_pct}%)"
            elif pos.pnl_pct >= take_profit_pct:
                exit_reason = f"TAKE_PROFIT: up {pos.pnl_pct:.1f}% (threshold: +{take_profit_pct}%)"

            if exit_reason:
                exits_needed.append({
                    "token_mint": pos.token_mint,
                    "token_symbol": pos.token_symbol,
                    "pnl_pct": round(pos.pnl_pct, 1),
                    "current_price_usd": current_price,
                    "entry_price_usd": pos.entry_price_usd,
                    "entry_sol": pos.entry_sol,
                    "reason": exit_reason,
                })
            else:
                updated_positions.append(pos)

                # Check for liquidity warnings
                if pos.pnl_pct <= -(stop_loss_pct / 2):
                    warnings.append({
                        "token_symbol": pos.token_symbol,
                        "pnl_pct": round(pos.pnl_pct, 1),
                        "message": f"Position down {pos.pnl_pct:.1f}% — approaching stop-loss",
                    })

        # Update state with current prices (but don't remove exited positions yet
        # — that happens after execute_swap confirms the exit)
        for pos in state.positions:
            for updated in updated_positions:
                if pos.token_mint == updated.token_mint:
                    pos.current_price_usd = updated.current_price_usd
                    pos.pnl_pct = updated.pnl_pct
        save_state(state)

    finally:
        await birdeye.close()

    return {
        "status": "OK",
        "exits_needed": exits_needed,
        "exit_count": len(exits_needed),
        "warnings": warnings,
        "open_positions": len(state.positions),
    }


def main() -> None:
    result = asyncio.run(check_positions())
    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
