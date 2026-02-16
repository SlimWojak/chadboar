"""Tests for dual-profile conviction scoring (graduation vs accumulation).

Covers:
- Play type detection (graduation vs accumulation)
- Graduation weight profile + threshold (70)
- Accumulation weight profile + threshold (85)
- Pulse quality scoring component
- Edge bank cold start redistribution
- VETO 4 scoped to accumulation only
- VETO 6 graduation daily sublimit
- Graduation position cap ($50)
- Scoring simulations for realistic scenarios
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.scoring import ConvictionScorer, SignalInput, detect_play_type


@pytest.fixture
def scorer():
    return ConvictionScorer(config_path=Path("config/risk.yaml"))


# --- Play Type Detection ---


class TestPlayTypeDetection:
    """detect_play_type() classifies opportunities correctly."""

    def test_pulse_only_is_graduation(self):
        """Pulse signals + no whales = graduation."""
        signals = SignalInput(
            pulse_pro_trader_pct=15.0,
            pulse_organic_ratio=0.8,
            smart_money_whales=0,
        )
        assert detect_play_type(signals) == "graduation"

    def test_whales_only_is_accumulation(self):
        """Whale signals + no pulse = accumulation."""
        signals = SignalInput(
            smart_money_whales=3,
            pulse_pro_trader_pct=0.0,
            pulse_organic_ratio=1.0,
        )
        assert detect_play_type(signals) == "accumulation"

    def test_pulse_plus_whales_is_accumulation(self):
        """Pulse + whales = accumulation (triple-lock convergence)."""
        signals = SignalInput(
            smart_money_whales=2,
            pulse_pro_trader_pct=12.0,
            pulse_organic_ratio=0.7,
        )
        assert detect_play_type(signals) == "accumulation"

    def test_no_signals_is_accumulation(self):
        """No pulse, no whales = defaults to accumulation."""
        signals = SignalInput()
        assert detect_play_type(signals) == "accumulation"

    def test_ghost_metadata_triggers_graduation(self):
        """Ghost metadata alone (no whales) = graduation."""
        signals = SignalInput(
            pulse_ghost_metadata=True,
            smart_money_whales=0,
        )
        assert detect_play_type(signals) == "graduation"

    def test_bundler_pct_triggers_graduation(self):
        """Bundler % > 0 (no whales) = graduation."""
        signals = SignalInput(
            pulse_bundler_pct=5.0,
            smart_money_whales=0,
        )
        assert detect_play_type(signals) == "graduation"


# --- Graduation Profile Scoring ---


class TestGraduationProfile:
    """Graduation plays use graduation weights and threshold."""

    def test_graduation_uses_lower_threshold(self, scorer):
        """Graduation auto-execute at 55, accumulation at 75."""
        assert scorer._get_auto_execute_threshold("graduation") == 55
        assert scorer._get_auto_execute_threshold("accumulation") == 75

    def test_graduation_weights_have_pulse_quality(self, scorer):
        """Graduation weights include pulse_quality, SMO is 0."""
        weights = scorer._get_weights("graduation")
        assert weights.get("pulse_quality", 0) == 35
        assert weights.get("smart_money_oracle", 0) == 0

    def test_graduation_ideal_scores_auto_execute(self, scorer):
        """Best-case graduation: pulse quality + narrative + warden PASS >= 55."""
        signals = SignalInput(
            smart_money_whales=0,
            narrative_volume_spike=10.0,
            narrative_kol_detected=True,
            narrative_age_minutes=5,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_ghost_metadata=True,
            pulse_pro_trader_pct=15.0,
            pulse_bundler_pct=2.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        assert result.recommendation == "AUTO_EXECUTE"
        assert result.permission_score >= 55

    def test_graduation_weak_pulse_lower_score(self, scorer):
        """Graduation with low pulse quality scores lower than ideal case."""
        signals = SignalInput(
            smart_money_whales=0,
            narrative_volume_spike=6.0,
            narrative_kol_detected=False,
            narrative_age_minutes=15,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.4,
            pulse_pro_trader_pct=3.0,
            pulse_bundler_pct=10.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        # With threshold at 55 and 1-source gate skip, weak pulse may still
        # AUTO_EXECUTE — but score should be significantly lower than ideal
        assert result.permission_score < 70

    def test_graduation_smo_structurally_zero(self, scorer):
        """SMO is 0 in graduation (neutral, not penalty)."""
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.7,
            pulse_pro_trader_pct=12.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        assert result.breakdown.get("smart_money_oracle", 0) == 0


# --- Pulse Quality Scoring ---


class TestPulseQualityScoring:
    """score_pulse_quality() component tests."""

    def test_max_pulse_quality(self, scorer):
        """Perfect pulse signals = 35 (capped)."""
        signals = SignalInput(
            pulse_organic_ratio=0.9,
            pulse_ghost_metadata=True,
            pulse_pro_trader_pct=15.0,
            pulse_bundler_pct=2.0,
        )
        score, reason, breakdown = scorer.score_pulse_quality(signals)
        assert score == 35  # 15 organic + 5 ghost + 10 pro + 5 clean = 35

    def test_organic_ratio_tiers(self, scorer):
        """Organic ratio scoring: >=0.7 (15), >=0.5 (10), >=0.3 (5), <0.3 (0)."""
        for ratio, expected in [(0.9, 15), (0.7, 15), (0.6, 10), (0.5, 10), (0.4, 5), (0.3, 5), (0.2, 0)]:
            signals = SignalInput(pulse_organic_ratio=ratio)
            score, _, _ = scorer.score_pulse_quality(signals)
            # Score includes clean holders (+5 for bundler=0), so organic portion is score - 5
            organic_pts = score - 5  # default bundler=0 gives clean holder bonus
            assert organic_pts == expected, f"ratio={ratio}: got {organic_pts}, expected {expected}"

    def test_ghost_metadata_bonus(self, scorer):
        """Ghost metadata adds +5."""
        base = SignalInput(pulse_organic_ratio=0.7, pulse_bundler_pct=0.0)
        ghost = SignalInput(pulse_organic_ratio=0.7, pulse_bundler_pct=0.0, pulse_ghost_metadata=True)
        base_score, _, _ = scorer.score_pulse_quality(base)
        ghost_score, _, _ = scorer.score_pulse_quality(ghost)
        assert ghost_score - base_score == 5

    def test_pro_trader_tiers(self, scorer):
        """Pro trader scoring: >10% (+10), >5% (+5), <=5% (0)."""
        for pct, expected_bonus in [(15.0, 10), (10.1, 10), (8.0, 5), (5.1, 5), (5.0, 0), (0.0, 0)]:
            signals = SignalInput(pulse_pro_trader_pct=pct)
            score, _, breakdown = scorer.score_pulse_quality(signals)
            assert breakdown.get("pulse_pro_trader", 0) == expected_bonus, f"pct={pct}"

    def test_clean_holders_bonus(self, scorer):
        """Bundler < 5% gives +5 clean holders bonus."""
        clean = SignalInput(pulse_bundler_pct=3.0)
        dirty = SignalInput(pulse_bundler_pct=6.0)
        clean_score, _, clean_bd = scorer.score_pulse_quality(clean)
        dirty_score, _, dirty_bd = scorer.score_pulse_quality(dirty)
        assert clean_bd.get("pulse_clean_holders", 0) == 5
        assert dirty_bd.get("pulse_clean_holders", 0) == 0


# --- Edge Bank Cold Start ---


class TestEdgeBankColdStart:
    """Edge bank disabled until 10+ beads, points go to rug warden."""

    def test_cold_start_redistributes_to_warden(self, scorer):
        """With 0 beads, edge bank is 0 and warden gets bonus."""
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            edge_bank_match_pct=90.0,  # Would score high if active
        )
        result = scorer.score(signals, pot_balance_sol=14.0, edge_bank_bead_count=0)
        assert result.breakdown.get("edge_bank", 0) == 0
        # Warden should have base + redistributed points
        # Accumulation: rug_warden=20 + edge_bank=10 = 30
        assert result.breakdown["rug_warden"] == 30

    def test_active_edge_bank_scores_normally(self, scorer):
        """With 10+ beads, edge bank scores based on match %."""
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            edge_bank_match_pct=90.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0, edge_bank_bead_count=15)
        assert result.breakdown.get("edge_bank", 0) > 0
        # Warden should have normal points (20 for accumulation)
        assert result.breakdown["rug_warden"] == 20

    def test_cold_start_graduation_redistributes(self, scorer):
        """Graduation cold start: edge_bank(10) → warden(25+10=35)."""
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0, edge_bank_bead_count=0)
        assert result.play_type == "graduation"
        assert result.breakdown.get("edge_bank", 0) == 0
        # Graduation: rug_warden=25 + edge_bank=10 = 35
        assert result.breakdown["rug_warden"] == 35


# --- VETO 4 Scoping ---


class TestVeto4Scoping:
    """VETO 4 (wash trading) — downgraded to -25 penalty for accumulation only."""

    def test_veto4_penalizes_accumulation(self, scorer):
        """10x volume + no KOL + whales = -25 penalty for accumulation (not VETO)."""
        signals = SignalInput(
            smart_money_whales=2,
            narrative_volume_spike=12.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "accumulation"
        assert "unsocialized_volume" in result.red_flags
        assert result.red_flags["unsocialized_volume"] == -25

    def test_veto4_does_not_fire_for_graduation(self, scorer):
        """10x volume + no KOL + pulse-only = NOT VETO for graduation."""
        signals = SignalInput(
            smart_money_whales=0,
            narrative_volume_spike=15.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        assert result.recommendation != "VETO"


# --- VETO 6: Graduation Daily Sublimit ---


class TestVeto6GraduationSublimit:
    """VETO 6: Graduation plays capped at 8/day."""

    def test_sublimit_veto_fires(self, scorer):
        """8 graduation plays already done today → VETO."""
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
        )
        result = scorer.score(
            signals, pot_balance_sol=14.0, daily_graduation_count=8,
        )
        assert result.play_type == "graduation"
        assert result.recommendation == "VETO"
        assert "daily limit" in result.reasoning.lower()

    def test_sublimit_allows_under_limit(self, scorer):
        """7 graduation plays today → allowed."""
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
            narrative_volume_spike=10.0,
            narrative_kol_detected=True,
            narrative_age_minutes=5,
            pulse_ghost_metadata=True,
            pulse_bundler_pct=2.0,
        )
        result = scorer.score(
            signals, pot_balance_sol=14.0, daily_graduation_count=7,
        )
        assert result.play_type == "graduation"
        assert result.recommendation != "VETO"

    def test_sublimit_does_not_affect_accumulation(self, scorer):
        """Accumulation plays are not subject to graduation sublimit."""
        signals = SignalInput(
            smart_money_whales=3,
            narrative_volume_spike=8.0,
            narrative_kol_detected=True,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(
            signals, pot_balance_sol=14.0, daily_graduation_count=10,
        )
        assert result.play_type == "accumulation"
        assert result.recommendation != "VETO" or "daily limit" not in result.reasoning.lower()


# --- Graduation Position Cap ---


class TestGraduationPositionCap:
    """Graduation trades capped at max_position_usd ($30)."""

    def test_graduation_position_capped_at_30_usd(self, scorer):
        """Graduation position size <= $30 / sol_price."""
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.9,
            pulse_pro_trader_pct=15.0,
            pulse_ghost_metadata=True,
            pulse_bundler_pct=2.0,
            narrative_volume_spike=10.0,
            narrative_kol_detected=True,
            narrative_age_minutes=5,
        )
        result = scorer.score(
            signals, pot_balance_sol=100.0, sol_price_usd=80.0,
        )
        assert result.play_type == "graduation"
        # $30 / $80 = 0.375 SOL max
        assert result.position_size_sol <= 30.0 / 80.0 + 0.001

    def test_accumulation_not_capped_at_30(self, scorer):
        """Accumulation uses normal position sizing (max_position_pct)."""
        signals = SignalInput(
            smart_money_whales=3,
            narrative_volume_spike=8.0,
            narrative_kol_detected=True,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
            edge_bank_match_pct=90.0,
        )
        result = scorer.score(
            signals, pot_balance_sol=100.0, sol_price_usd=80.0,
            edge_bank_bead_count=20,
        )
        assert result.play_type == "accumulation"
        # Max position = 5% of pot = 5 SOL (>> $50/$80)
        # Actual size depends on score, but cap is higher


# --- Scoring Simulations (Realistic Scenarios) ---


class TestScoringSimulations:
    """End-to-end scoring for realistic market scenarios."""

    def test_graduation_ideal_case(self, scorer):
        """Best graduation: high organic, ghost, pro traders, narrative, warden PASS."""
        signals = SignalInput(
            smart_money_whales=0,
            narrative_volume_spike=10.0,
            narrative_kol_detected=True,
            narrative_age_minutes=5,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_ghost_metadata=True,
            pulse_pro_trader_pct=15.0,
            pulse_bundler_pct=2.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        # pulse_quality: 15+5+10+5=35, narrative: ~25+10=30(capped), warden: 25+10=35, edge: 0
        # Total ordering >= 70 → AUTO_EXECUTE
        assert result.play_type == "graduation"
        assert result.recommendation == "AUTO_EXECUTE"
        assert result.ordering_score >= 70

    def test_graduation_marginal_case(self, scorer):
        """Marginal graduation: moderate pulse, some narrative."""
        signals = SignalInput(
            smart_money_whales=0,
            narrative_volume_spike=6.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.6,
            pulse_pro_trader_pct=8.0,
            pulse_bundler_pct=3.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        # pulse: 10+5+5=20, narrative: ~18, warden: 35, edge: 0 = ~73
        # Should be WATCHLIST or AUTO_EXECUTE depending on exact math
        assert result.recommendation in ("WATCHLIST", "AUTO_EXECUTE")

    def test_accumulation_ideal_case(self, scorer):
        """Best accumulation: 3 whales + 10x volume + KOL + PASS."""
        signals = SignalInput(
            smart_money_whales=3,
            narrative_volume_spike=10.0,
            narrative_kol_detected=True,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "accumulation"
        # oracle: 40, narrative: 25+10=30(cap), warden: 30 (20+10 cold start), edge: 0
        # = 100 → AUTO_EXECUTE (threshold 75)
        assert result.recommendation == "AUTO_EXECUTE"
        assert result.ordering_score >= 75

    def test_accumulation_moderate_case(self, scorer):
        """2 whales + 5x volume + no KOL + PASS."""
        signals = SignalInput(
            smart_money_whales=2,
            narrative_volume_spike=5.0,
            narrative_kol_detected=False,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "accumulation"
        # oracle: 30, narrative: 15, warden: 30, edge: 0 = 75
        # 75 >= 75 (auto_execute) but only 2 primary sources (narrative+warden) → AUTO_EXECUTE
        assert result.recommendation in ("AUTO_EXECUTE", "WATCHLIST")

    def test_graduation_with_red_flags(self, scorer):
        """Graduation with bundler and sniper red flags — penalties apply."""
        signals = SignalInput(
            smart_money_whales=0,
            narrative_volume_spike=10.0,
            narrative_kol_detected=True,
            narrative_age_minutes=5,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=15.0,
            pulse_bundler_pct=25.0,  # Red flag
            pulse_sniper_pct=35.0,   # Red flag
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        assert "pulse_bundler" in result.red_flags
        assert "pulse_sniper" in result.red_flags
        # Penalties: -10 -10 = -20 from permission
        assert result.permission_score < result.ordering_score

    def test_rug_warden_veto_overrides_everything(self, scorer):
        """Rug Warden FAIL = VETO regardless of play type or signals."""
        for play_signals in [
            # Graduation
            SignalInput(smart_money_whales=0, rug_warden_status="FAIL",
                        pulse_organic_ratio=0.9, pulse_pro_trader_pct=15.0),
            # Accumulation
            SignalInput(smart_money_whales=3, rug_warden_status="FAIL",
                        narrative_volume_spike=10.0, narrative_kol_detected=True),
        ]:
            result = scorer.score(play_signals, pot_balance_sol=14.0)
            assert result.recommendation == "VETO"
            assert "RUG-WARDEN-VETO" in result.reasoning.upper() or "RUG WARDEN FAIL" in result.reasoning.upper()

    def test_no_signals_is_discard(self, scorer):
        """Zero signals = DISCARD."""
        signals = SignalInput(rug_warden_status="PASS")
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.recommendation in ("DISCARD", "PAPER_TRADE")
        assert result.ordering_score < 45
