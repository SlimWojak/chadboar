"""Tests for AutistBoar guard layer.

Covers:
- Killswitch detection (INV-KILLSWITCH)
- Drawdown guard (INV-DRAWDOWN-50)
- Risk limits (INV-DAILY-EXPOSURE-30)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.guards.killswitch import KILLSWITCH_FILE, check_killswitch
from lib.guards.drawdown import check_drawdown
from lib.guards.risk import check_risk
from lib.state import STATE_PATH, State, save_state

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Redirect state and killswitch paths to tmp for test isolation."""
    # Redirect STATE_PATH
    test_state = tmp_path / "state" / "state.json"
    test_state.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("lib.state.STATE_PATH", test_state)
    monkeypatch.setattr("lib.state.LATEST_PATH", tmp_path / "state" / "latest.md")
    monkeypatch.setattr("lib.guards.drawdown.load_state", lambda: _load_from(test_state))
    monkeypatch.setattr("lib.guards.drawdown.save_state", lambda s: _save_to(test_state, s))
    monkeypatch.setattr("lib.guards.risk.load_state", lambda: _load_from(test_state))
    monkeypatch.setattr("lib.guards.risk.save_state", lambda s: _save_to(test_state, s))

    # Redirect KILLSWITCH_FILE
    test_ks = tmp_path / "killswitch.txt"
    monkeypatch.setattr("lib.guards.killswitch.KILLSWITCH_FILE", test_ks)

    yield tmp_path


def _load_from(path: Path) -> State:
    if path.exists():
        return State(**json.loads(path.read_text()))
    return State()


def _save_to(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.model_dump(), indent=2))


def _write_state(tmp_path: Path, **overrides) -> None:
    # Default daily_date to today so check_daily_reset doesn't wipe counters
    if "daily_date" not in overrides:
        overrides["daily_date"] = TODAY
    state = State(**overrides)
    state_path = tmp_path / "state" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state.model_dump(), indent=2))


# ── Killswitch Tests ─────────────────────────────────────────────────


class TestKillswitch:
    """INV-KILLSWITCH: killswitch.txt → immediate halt."""

    def test_no_killswitch(self, clean_state):
        result = check_killswitch()
        assert result["status"] == "CLEAR"

    def test_killswitch_active(self, clean_state):
        ks_file = clean_state / "killswitch.txt"
        ks_file.write_text("Emergency halt — G")
        # Re-import to get monkeypatched path
        from lib.guards.killswitch import check_killswitch as ck
        result = ck()
        assert result["status"] == "ACTIVE"
        assert "Emergency halt" in result["message"]

    def test_killswitch_empty_file(self, clean_state):
        ks_file = clean_state / "killswitch.txt"
        ks_file.write_text("")
        from lib.guards.killswitch import check_killswitch as ck
        result = ck()
        assert result["status"] == "ACTIVE"


# ── Drawdown Tests ───────────────────────────────────────────────────


class TestDrawdown:
    """INV-DRAWDOWN-50: pot < 50% of starting → halt 24h."""

    def test_no_starting_balance(self, clean_state):
        _write_state(clean_state, starting_balance_sol=0.0)
        result = check_drawdown()
        assert result["status"] == "CLEAR"

    def test_above_threshold(self, clean_state):
        _write_state(
            clean_state,
            starting_balance_sol=10.0,
            current_balance_sol=7.0,
        )
        result = check_drawdown()
        assert result["status"] == "CLEAR"
        assert result["current_pct"] == 70.0

    def test_below_threshold_triggers_halt(self, clean_state):
        _write_state(
            clean_state,
            starting_balance_sol=10.0,
            current_balance_sol=4.0,
        )
        result = check_drawdown()
        assert result["status"] == "HALTED"
        assert result.get("alert") is True
        assert result["current_pct"] == 40.0

    def test_at_exact_threshold(self, clean_state):
        _write_state(
            clean_state,
            starting_balance_sol=10.0,
            current_balance_sol=5.0,
        )
        result = check_drawdown()
        assert result["status"] == "HALTED"


# ── Risk Limit Tests ─────────────────────────────────────────────────


class TestRisk:
    """INV-DAILY-EXPOSURE-30 + circuit breakers."""

    def test_clear_when_fresh(self, clean_state):
        _write_state(
            clean_state,
            current_balance_sol=10.0,
            daily_exposure_sol=0.0,
        )
        result = check_risk()
        assert result["status"] == "CLEAR"

    def test_daily_exposure_blocked(self, clean_state):
        _write_state(
            clean_state,
            current_balance_sol=10.0,
            daily_exposure_sol=3.5,  # 35% > 30%
        )
        result = check_risk()
        assert result["status"] == "BLOCKED"
        assert any("Daily exposure" in i for i in result["issues"])

    def test_max_positions_blocked(self, clean_state):
        positions = [
            {
                "token_mint": f"mint{i}",
                "token_symbol": f"TK{i}",
                "entry_price_usd": 0.01,
                "entry_sol": 0.5,
                "entry_time": "2026-02-10T00:00:00Z",
            }
            for i in range(5)
        ]
        _write_state(
            clean_state,
            current_balance_sol=10.0,
            positions=positions,
        )
        result = check_risk()
        assert result["status"] == "BLOCKED"
        assert result["open_positions"] == 5

    def test_consecutive_losses_warning(self, clean_state):
        _write_state(
            clean_state,
            current_balance_sol=10.0,
            consecutive_losses=4,
        )
        result = check_risk()
        assert result["status"] == "WARNING"
        assert any("Consecutive losses" in w for w in result["warnings"])

    def test_daily_loss_halts(self, clean_state):
        _write_state(
            clean_state,
            current_balance_sol=10.0,
            daily_loss_pct=12.0,  # > 10% threshold
        )
        result = check_risk()
        assert result["status"] == "BLOCKED"
