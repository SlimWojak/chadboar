"""Tests for VSM S2 Divergence Damping in conviction scoring.

Covers:
- RED FLAG 5: Oracle ↔ Narrative mismatch detection
- Threshold boundaries (whales, volume, KOL exemption)
- Interaction with existing penalties (partial data, DISCARD forcing)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.scoring import ConvictionScorer, SignalInput


@pytest.fixture
def scorer():
    return ConvictionScorer(config_path=Path("config/risk.yaml"))


class TestDivergenceDamping:
    """S2 Divergence Damping: whale accumulation without narrative → −25 pts."""

    def test_divergence_damping_fires(self, scorer):
        """3 whales + 0 volume → red_flags has 'divergence_damping', −25 applied."""
        signals = SignalInput(
            smart_money_whales=3,
            narrative_volume_spike=0.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)

        assert 'divergence_damping' in result.red_flags
        assert result.red_flags['divergence_damping'] == -25
        assert "S2 DAMPING" in result.reasoning

    def test_divergence_damping_no_fire_with_narrative(self, scorer):
        """3 whales + 6x volume → no damping flag (narrative present)."""
        signals = SignalInput(
            smart_money_whales=3,
            narrative_volume_spike=6.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)

        assert 'divergence_damping' not in result.red_flags

    def test_divergence_damping_no_fire_low_whales(self, scorer):
        """1 whale + 0 volume → no damping (below 2-whale threshold)."""
        signals = SignalInput(
            smart_money_whales=1,
            narrative_volume_spike=0.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)

        assert 'divergence_damping' not in result.red_flags

    def test_divergence_damping_forces_low_recommendation(self, scorer):
        """2 whales + PASS warden + 0 narrative → damped below auto-execute."""
        signals = SignalInput(
            smart_money_whales=2,
            narrative_volume_spike=0.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
            edge_bank_match_pct=0.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)

        # 2 whales (30) + warden PASS (30) = 60 ordering
        # 60 - 25 damping = 35 permission → PAPER_TRADE (>30 paper threshold, <45 watchlist)
        assert result.recommendation in ("PAPER_TRADE", "DISCARD")
        assert 'divergence_damping' in result.red_flags
        assert result.permission_score < 45  # never reaches WATCHLIST

    def test_divergence_damping_stacks_with_partial_data(self, scorer):
        """Damping + data_completeness=0.8 both apply (orthogonal triggers)."""
        signals = SignalInput(
            smart_money_whales=3,
            narrative_volume_spike=0.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
            edge_bank_match_pct=80.0,
        )
        # Score without partial data penalty
        result_full = scorer.score(signals, pot_balance_sol=14.0, data_completeness=1.0)
        # Score with partial data penalty
        result_partial = scorer.score(signals, pot_balance_sol=14.0, data_completeness=0.8)

        # Both should have damping
        assert 'divergence_damping' in result_full.red_flags
        assert 'divergence_damping' in result_partial.red_flags
        # Partial data should further reduce permission score
        assert result_partial.permission_score < result_full.permission_score

    def test_divergence_damping_kol_exemption(self, scorer):
        """2 whales + 0 volume + KOL detected → no damping (KOL exemption)."""
        signals = SignalInput(
            smart_money_whales=2,
            narrative_volume_spike=0.0,
            narrative_kol_detected=True,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)

        assert 'divergence_damping' not in result.red_flags
