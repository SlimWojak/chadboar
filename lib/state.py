"""State management for ChadBoar.

Reads and writes state/state.json — the single source of truth for
portfolio state, positions, daily exposure, and halt status.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

WORKSPACE = Path(__file__).resolve().parent.parent
STATE_PATH = WORKSPACE / "state" / "state.json"
LATEST_PATH = WORKSPACE / "state" / "latest.md"


class Position(BaseModel):
    """A single open position."""

    token_mint: str
    token_symbol: str
    direction: str = "long"
    entry_price_usd: float
    entry_sol: float
    entry_time: str
    current_price_usd: float = 0.0
    pnl_pct: float = 0.0
    thesis: str = ""
    signals: list[str] = Field(default_factory=list)


class State(BaseModel):
    """Portfolio state — serialized to state/state.json."""

    # Pot
    starting_balance_sol: float = 0.0
    current_balance_sol: float = 0.0
    current_balance_usd: float = 0.0
    sol_price_usd: float = 0.0

    # Positions
    positions: list[Position] = Field(default_factory=list)

    # Daily tracking (reset at midnight UTC)
    daily_exposure_sol: float = 0.0
    daily_date: str = ""
    daily_loss_pct: float = 0.0
    consecutive_losses: int = 0

    # Halt state
    halted: bool = False
    halted_at: str = ""
    halt_reason: str = ""

    # Stats
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    last_trade_time: str = ""
    last_heartbeat_time: str = ""

    # Dry-run mode
    dry_run_mode: bool = False
    dry_run_cycles_completed: int = 0
    dry_run_target_cycles: int = 10


def load_state() -> State:
    """Load state from disk. Returns default state if file doesn't exist."""
    if STATE_PATH.exists():
        data = json.loads(STATE_PATH.read_text())
        return State(**data)
    return State()


def save_state(state: State) -> None:
    """Write state to disk atomically."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.model_dump(), indent=2))
    tmp.rename(STATE_PATH)


def update_latest(state: State) -> None:
    """Write human-readable summary to state/latest.md."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    positions_text = "None" if not state.positions else ""
    for p in state.positions:
        positions_text += (
            f"\n- {p.token_symbol} ({p.token_mint[:8]}...): "
            f"entry ${p.entry_price_usd:.6f}, "
            f"current ${p.current_price_usd:.6f}, "
            f"PnL {p.pnl_pct:+.1f}%"
        )

    pnl_pct = (
        ((state.current_balance_sol - state.starting_balance_sol) / state.starting_balance_sol * 100)
        if state.starting_balance_sol > 0
        else 0.0
    )

    # Chain health
    chain_text = ""
    try:
        from lib.chain.bead_chain import get_chain_stats
        chain = get_chain_stats()
        chain_status = "CLEAN"
        anchor_info = "None"
        if chain["last_anchor"]:
            tx = chain["last_anchor"]["tx_signature"]
            anchor_info = f"tx {tx[:12]}... (seq {chain['last_anchor']['seq']})"
        chain_text = f"""
## Chain Health
- Status: {chain_status}
- Chain length: {chain['chain_length']:,} beads
- Last anchor: {anchor_info}
- Beads since anchor: {chain['beads_since_anchor']}
"""
    except Exception:
        chain_text = ""

    content = f"""# ChadBoar — Latest State
Updated: {now}

## Portfolio
- Starting: {state.starting_balance_sol:.4f} SOL
- Current: {state.current_balance_sol:.4f} SOL (${state.current_balance_usd:.2f})
- SOL price: ${state.sol_price_usd:.2f}
- Overall PnL: {pnl_pct:+.1f}%

## Open Positions ({len(state.positions)}/{5})
{positions_text}

## Today
- Daily exposure: {state.daily_exposure_sol:.4f} SOL
- Daily losses: {state.daily_loss_pct:.1f}%
- Consecutive losses: {state.consecutive_losses}

## Status
- Halted: {"YES — " + state.halt_reason if state.halted else "No"}
- Total trades: {state.total_trades} (W: {state.total_wins} / L: {state.total_losses})
- Last trade: {state.last_trade_time or "Never"}
- Last heartbeat: {state.last_heartbeat_time or "Never"}
{chain_text}"""
    LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(content)


def check_daily_reset(state: State) -> State:
    """Reset daily counters if date has changed."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.daily_date != today:
        state.daily_date = today
        state.daily_exposure_sol = 0.0
        state.daily_loss_pct = 0.0
    return state
