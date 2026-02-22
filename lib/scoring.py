#!/usr/bin/env python3
"""
Conviction Scoring System — Dual-profile play-type routing.

Two fundamentally different play types:
  - Graduation (speed): Pulse-sourced PumpFun → Raydium migrations. Minutes, not hours.
  - Accumulation (conviction): Nansen whale accumulation. Hours to days.

Each gets its own weight profile, threshold, and position cap.
"""
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class SignalInput:
    """Input signals from various detectors."""
    smart_money_whales: int = 0          # Number of distinct whales accumulating
    narrative_volume_spike: float = 0.0  # Volume multiple vs average
    narrative_kol_detected: bool = False
    narrative_age_minutes: int = 0       # Age of narrative signal
    rug_warden_status: str = "UNKNOWN"   # PASS, WARN, FAIL
    edge_bank_match_pct: float = 0.0     # Similarity to past winners
    # TGM flow intelligence fields
    exchange_outflow_usd: float = 0.0       # From flow_intel (negative = accumulation)
    fresh_wallet_inflow_usd: float = 0.0    # From flow_intel (red flag indicator)
    smart_money_buy_volume_usd: float = 0.0 # From buyer_depth
    dca_count: int = 0                       # Active smart money DCAs
    # Mobula Pulse fields (Phase 0)
    pulse_ghost_metadata: bool = False       # No socials but high volume (stealth launch)
    pulse_organic_ratio: float = 1.0         # organic_volume / total_volume (0.0-1.0)
    pulse_bundler_pct: float = 0.0           # Bundler holdings % (red flag > 20%)
    pulse_sniper_pct: float = 0.0            # Sniper holdings % (red flag > 30%)
    pulse_pro_trader_pct: float = 0.0        # Pro trader + smart trader holdings %
    pulse_deployer_migrations: int = 0       # Deployer's prior migrations (rug risk > 3)
    pulse_stage: str = ""                     # "bonded" | "bonding" | "" (from Pulse)
    # Enrichment signals (boost only — defaults never penalize)
    holder_delta_pct: float = 0.0              # Birdeye: holder count change (positive = growing)
    entry_market_cap_usd: float = 0.0          # For mcap-aware exit tiers
    pulse_trending_score: float = 0.0          # Mobula: trending score (1h)
    pulse_dexscreener_boosted: bool = False    # Mobula: DS boost detected


@dataclass
class ConvictionScore:
    """Output conviction score with breakdown."""
    ordering_score: int      # Pure signal strength (for learning)
    permission_score: int    # After penalties and gates (for action)
    breakdown: Dict[str, int]
    red_flags: Dict[str, int]  # Negative contributions
    primary_sources: List[str]  # Which primary sources triggered
    recommendation: str  # AUTO_EXECUTE, WATCHLIST, DISCARD, VETO
    position_size_sol: float
    reasoning: str
    play_type: str = "accumulation"  # graduation | accumulation


def detect_play_type(signals: SignalInput) -> str:
    """Classify opportunity as graduation or accumulation play.

    Graduation: Pulse-sourced token with no whale data (brand new token).
    Accumulation: Established token with whale signals.
    """
    has_pulse = (
        signals.pulse_pro_trader_pct > 0
        or signals.pulse_ghost_metadata
        or signals.pulse_organic_ratio < 1.0
        or signals.pulse_bundler_pct > 0
        or signals.pulse_sniper_pct > 0
        or signals.pulse_deployer_migrations > 0
        or signals.pulse_stage in ("bonded", "bonding")
    )
    has_whales = signals.smart_money_whales >= 1

    if has_pulse and not has_whales:
        return "graduation"
    # Triple-lock convergence (pulse + whales) or whale-only → accumulation
    return "accumulation"


class ConvictionScorer:
    """Calculate conviction scores from signal inputs."""

    def __init__(self, config_path: Path = Path("config/risk.yaml")):
        """Load scoring configuration."""
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.weights = self.config['conviction']['weights']
        self.weights_graduation = self.config['conviction'].get('weights_graduation', self.weights)
        self.thresholds = self.config['conviction']['thresholds']
        self.sizing = self.config['conviction']['sizing']
        self.portfolio = self.config['portfolio']
        self.trade_limits = self.config['trade']
        self.graduation_config = self.config['conviction'].get('graduation', {})
        self.edge_bank_min_beads = self.config['conviction'].get('edge_bank_min_beads', 10)

    def _get_weights(self, play_type: str) -> dict:
        """Get weight profile for play type."""
        if play_type == "graduation":
            return dict(self.weights_graduation)
        return dict(self.weights)

    def _get_auto_execute_threshold(self, play_type: str) -> int:
        """Get auto-execute threshold for play type."""
        if play_type == "graduation":
            return self.thresholds.get('auto_execute_graduation', 70)
        return self.thresholds['auto_execute']

    def score_smart_money_oracle(self, whales: int, max_points: int = 40) -> tuple[int, str]:
        """Score whale accumulation signals."""
        if whales == 0:
            return 0, "No whale accumulation detected"

        # +15 per whale, cap at max_points
        score = min(whales * 15, max_points)

        if whales >= 3:
            return score, f"{whales} distinct whales accumulating (max points)"
        else:
            return score, f"{whales} whale(s) detected (+15 each)"

    def score_narrative_hunter(
        self,
        volume_spike: float,
        kol_detected: bool,
        age_minutes: int,
        max_points: int = 30,
    ) -> tuple[int, str]:
        """Score social momentum + volume signals.

        Gradient volume scoring (was binary 5x cutoff):
          2x = 5pts, 3x = 10pts, 5x = 15pts, 10x = 20pts, 20x+ = 25pts
        This lets lower-volume candidates still earn partial narrative points,
        which is critical when whale signals are thin.
        """
        # No signal at all
        if volume_spike < 2.0 and not kol_detected:
            return 0, "No narrative momentum"

        # Gradient volume scoring
        if volume_spike >= 20.0:
            base = 25
        elif volume_spike >= 10.0:
            base = 20
        elif volume_spike >= 5.0:
            base = 15
        elif volume_spike >= 3.0:
            base = 10
        elif volume_spike >= 2.0:
            base = 5
        else:
            base = 0

        # KOL bonus
        kol_bonus = 10 if kol_detected else 0

        # Time decay: full points until 30min, then decay to 0 at 60min
        if age_minutes <= 30:
            decay_factor = 1.0
        elif age_minutes < 60:
            decay_factor = 1.0 - ((age_minutes - 30) / 30)
        else:
            decay_factor = 0.0

        score = int((base + kol_bonus) * decay_factor)
        score = min(score, max_points)

        reasoning_parts = []
        if volume_spike >= 2.0:
            reasoning_parts.append(f"{volume_spike:.1f}x volume spike")
        if kol_detected:
            reasoning_parts.append("KOL detected")
        if age_minutes > 30:
            reasoning_parts.append(f"decayed ({age_minutes}min old)")

        reasoning = ", ".join(reasoning_parts) if reasoning_parts else "No narrative signal"

        return score, reasoning

    def score_rug_warden(self, status: str, max_points: int = 20, play_type: str = "accumulation") -> tuple[int, str]:
        """Score Rug Warden validation.

        For graduation plays, WARN gives 75% instead of 50% — PumpFun tokens
        commonly trigger WARN for unlocked LP and holder concentration, which
        are expected characteristics, not red flags.
        """
        if status == "PASS":
            return max_points, "Rug Warden: PASS"
        elif status == "WARN":
            warn_pct = 0.75 if play_type == "graduation" else 0.5
            return int(max_points * warn_pct), f"Rug Warden: WARN ({int(warn_pct*100)}% pts)"
        else:  # FAIL or UNKNOWN
            return 0, f"Rug Warden: {status}"

    def score_edge_bank(self, match_pct: float, max_points: int = 10) -> tuple[int, str]:
        """Score historical pattern match."""
        if match_pct < 70.0:
            return 0, "No strong historical match"

        # Linear scale from 70% (5pts) to 100% (max_points)
        score = int(((match_pct - 70) / 30) * max_points)
        score = min(score, max_points)

        return score, f"{match_pct:.0f}% match to past winners"

    def score_pulse_quality(
        self,
        signals: SignalInput,
        max_points: int = 35,
    ) -> tuple[int, str, dict]:
        """Score Pulse quality signals (graduation profile).

        Combines ghost metadata, pro trader %, organic ratio into
        a single score component for the graduation weight profile.
        """
        score = 0
        parts = []
        breakdown_extra = {}

        # Base: organic ratio quality (0-15 pts)
        if signals.pulse_organic_ratio >= 0.7:
            organic_pts = 15
        elif signals.pulse_organic_ratio >= 0.5:
            organic_pts = 10
        elif signals.pulse_organic_ratio >= 0.3:
            organic_pts = 5
        else:
            organic_pts = 0
        score += organic_pts
        breakdown_extra['pulse_organic'] = organic_pts
        if organic_pts > 0:
            parts.append(f"organic {signals.pulse_organic_ratio:.0%}")

        # Ghost metadata bonus (+5)
        if signals.pulse_ghost_metadata:
            score += 5
            breakdown_extra['pulse_ghost'] = 5
            parts.append("ghost metadata")

        # Pro trader holdings (+10 if >10%, +5 if >5%)
        if signals.pulse_pro_trader_pct > 10:
            pro_pts = 10
        elif signals.pulse_pro_trader_pct > 5:
            pro_pts = 5
        else:
            pro_pts = 0
        score += pro_pts
        breakdown_extra['pulse_pro_trader'] = pro_pts
        if pro_pts > 0:
            parts.append(f"pro traders {signals.pulse_pro_trader_pct:.1f}%")

        # Low bundler bonus (+5 if <5%)
        if signals.pulse_bundler_pct < 5:
            score += 5
            breakdown_extra['pulse_clean_holders'] = 5
            parts.append("clean holders")

        # Stage bonus: bonding (pre-graduation) gets the bonus.
        # Bonded (post-graduation) gets NO bonus — data shows -45% avg PnL,
        # fast money already exited. Penalty applied separately as red flag.
        bonded_bonus = self.graduation_config.get('bonded_stage_bonus', 5)
        if signals.pulse_stage == "bonding" and bonded_bonus > 0:
            score += bonded_bonus
            breakdown_extra['pulse_bonding_bonus'] = bonded_bonus
            parts.append(f"bonding +{bonded_bonus}")

        score = min(score, max_points)
        reasoning = f"Pulse: {', '.join(parts)}" if parts else "Pulse: no quality signals"

        return score, reasoning, breakdown_extra

    def calculate_position_size(
        self,
        score: int,
        pot_balance_sol: float,
        volatility_factor: float = 1.0,
        play_type: str = "accumulation",
        sol_price_usd: float = 78.0,
    ) -> float:
        """Calculate position size based on conviction score and play type."""
        # Formula: size = (score / 100) x (pot x 0.01) x (1 / volatility_factor)
        base_size = (score / 100) * (pot_balance_sol * self.sizing['base_multiplier'])
        adjusted_size = base_size / volatility_factor

        # Cap at max_position_pct
        max_size = pot_balance_sol * (self.trade_limits['max_position_pct'] / 100)
        size = min(adjusted_size, max_size)

        # Graduation hard cap: max_position_usd (default $50)
        if play_type == "graduation":
            grad_max_usd = self.graduation_config.get('max_position_usd', 50)
            grad_max_sol = grad_max_usd / sol_price_usd if sol_price_usd > 0 else 0.65
            size = min(size, grad_max_sol)

        return size

    def score(
        self,
        signals: SignalInput,
        pot_balance_sol: float,
        volatility_factor: float = 1.0,
        data_completeness: float = 1.0,
        concentrated_volume: bool = False,
        dumper_wallet_count: int = 0,
        time_mismatch: bool = False,
        edge_bank_bead_count: int = 0,
        daily_graduation_count: int = 0,
        sol_price_usd: float = 78.0,
    ) -> ConvictionScore:
        """
        Calculate total conviction score and recommendation.

        Play-type routing: detects graduation vs accumulation and applies
        the appropriate weight profile, threshold, and position cap.
        """
        breakdown = {}
        red_flags = {}
        primary_sources = []
        reasoning_parts = []

        # Detect play type
        play_type = detect_play_type(signals)

        # VETO CHECKS (apply to ALL play types)

        # VETO 1: Rug Warden FAIL (INV-RUG-WARDEN-VETO)
        if signals.rug_warden_status == "FAIL":
            return ConvictionScore(
                ordering_score=0,
                permission_score=0,
                breakdown={"rug_warden": 0},
                red_flags={},
                primary_sources=[],
                recommendation="VETO",
                position_size_sol=0.0,
                reasoning="VETO: Rug Warden FAIL (INV-RUG-WARDEN-VETO)",
                play_type=play_type,
            )

        # VETO 2: All whales are dumpers (checked in red flag section below)

        # VETO 3: Token too new (<2min)
        if signals.narrative_age_minutes < 2 and signals.narrative_volume_spike >= 5.0:
            return ConvictionScore(
                ordering_score=0,
                permission_score=0,
                breakdown={},
                red_flags={},
                primary_sources=[],
                recommendation="VETO",
                position_size_sol=0.0,
                reasoning="VETO: Token created <2min ago (too new for organic discovery)",
                play_type=play_type,
            )

        # RED FLAG: Volume spike >=10x with near-zero social — ACCUMULATION ONLY
        # Downgraded from VETO to -25 penalty: X API is disabled so kol_detected
        # is always false. On-chain volume without social confirmation is suspicious
        # but not an absolute block — Rug Warden is the safety gate.
        # Penalty applied later in red flags section (see 'unsocialized_volume').

        # VETO 4: Serial deployer (data shows -26% avg PnL — worst red flag by far)
        if signals.pulse_deployer_migrations > 5:
            return ConvictionScore(
                ordering_score=0,
                permission_score=0,
                breakdown={},
                red_flags={"pulse_serial_deployer": -100},
                primary_sources=[],
                recommendation="VETO",
                position_size_sol=0.0,
                reasoning=f"VETO: Serial deployer ({signals.pulse_deployer_migrations} prior migrations — rug trap pattern)",
                play_type=play_type,
            )

        # VETO 5: Pulse-bonded tokens (post-graduation)
        # Data: 40.9% rug rate, -24% avg PnL, 15.9% win rate.
        # Pre-bonding (bonding) is the play; post-bonding = fast money already exited.
        if signals.pulse_stage == "bonded":
            return ConvictionScore(
                ordering_score=0,
                permission_score=0,
                breakdown={},
                red_flags={"pulse_post_bonding": -100},
                primary_sources=[],
                recommendation="VETO",
                position_size_sol=0.0,
                reasoning="VETO: Post-bonding token (40.9% rug rate, -24% avg PnL — fast money already exited)",
                play_type=play_type,
            )

        # VETO 6: Graduation daily sublimit exceeded
        grad_max_daily = self.graduation_config.get('max_daily_plays', 3)
        if play_type == "graduation" and daily_graduation_count >= grad_max_daily:
            return ConvictionScore(
                ordering_score=0,
                permission_score=0,
                breakdown={},
                red_flags={},
                primary_sources=[],
                recommendation="VETO",
                position_size_sol=0.0,
                reasoning=f"VETO: Graduation daily limit reached ({daily_graduation_count}/{grad_max_daily})",
                play_type=play_type,
            )

        # VETO 7: Graduation mcap too high — not a micro-cap speed play
        grad_max_mcap = self.graduation_config.get('max_mcap_graduation', 500_000)
        if play_type == "graduation" and signals.entry_market_cap_usd > grad_max_mcap:
            return ConvictionScore(
                ordering_score=0,
                permission_score=0,
                breakdown={},
                red_flags={},
                primary_sources=[],
                recommendation="VETO",
                position_size_sol=0.0,
                reasoning=f"VETO: Graduation mcap ${signals.entry_market_cap_usd:,.0f} > ${grad_max_mcap:,.0f} cap",
                play_type=play_type,
            )

        # Get weight profile for play type
        weights = self._get_weights(play_type)

        # Edge bank cold start: if <10 beads, redistribute points to rug_warden
        edge_bank_active = edge_bank_bead_count >= self.edge_bank_min_beads
        if not edge_bank_active:
            warden_bonus = weights.get('edge_bank', 0)
            weights['rug_warden'] = weights.get('rug_warden', 20) + warden_bonus
            weights['edge_bank'] = 0

        # --- SCORE COMPONENTS ---

        if play_type == "graduation":
            # GRADUATION PROFILE: Pulse quality is the primary signal
            pulse_score, pulse_reason, pulse_extra = self.score_pulse_quality(
                signals, max_points=weights.get('pulse_quality', 35),
            )
            breakdown['pulse_quality'] = pulse_score
            breakdown.update(pulse_extra)
            reasoning_parts.append(pulse_reason)

            # PRIMARY SOURCE: Pulse (organic >= 0.3 and some quality signal)
            if pulse_score >= 15:
                primary_sources.append("pulse")

            # Narrative
            narrative_score, narrative_reason = self.score_narrative_hunter(
                signals.narrative_volume_spike,
                signals.narrative_kol_detected,
                signals.narrative_age_minutes,
                max_points=weights.get('narrative_hunter', 30),
            )
            breakdown['narrative_hunter'] = narrative_score
            reasoning_parts.append(f"Narrative: {narrative_reason}")
            if signals.narrative_volume_spike >= 3.0:
                primary_sources.append("narrative")

            # Rug Warden
            warden_score, warden_reason = self.score_rug_warden(
                signals.rug_warden_status,
                max_points=weights.get('rug_warden', 25),
                play_type="graduation",
            )
            breakdown['rug_warden'] = warden_score
            reasoning_parts.append(f"Warden: {warden_reason}")
            if signals.rug_warden_status in ["PASS", "WARN"]:
                primary_sources.append("warden")

            # SMO: structurally 0 for graduation (neutral, not penalty)
            breakdown['smart_money_oracle'] = 0

            # Edge Bank
            if edge_bank_active:
                edge_score, edge_reason = self.score_edge_bank(
                    signals.edge_bank_match_pct,
                    max_points=weights.get('edge_bank', 10),
                )
                breakdown['edge_bank'] = edge_score
                reasoning_parts.append(f"Edge: {edge_reason}")
            else:
                breakdown['edge_bank'] = 0
                reasoning_parts.append(f"Edge: cold start (warden +{warden_bonus}pts)")

            reasoning_parts.insert(0, "[GRADUATION]")

        else:
            # ACCUMULATION PROFILE: SMO is the primary signal
            oracle_score, oracle_reason = self.score_smart_money_oracle(
                signals.smart_money_whales,
                max_points=weights.get('smart_money_oracle', 40),
            )
            breakdown['smart_money_oracle'] = oracle_score
            reasoning_parts.append(f"Oracle: {oracle_reason}")
            if signals.smart_money_whales >= 1:
                primary_sources.append("oracle")

            # Narrative
            narrative_score, narrative_reason = self.score_narrative_hunter(
                signals.narrative_volume_spike,
                signals.narrative_kol_detected,
                signals.narrative_age_minutes,
                max_points=weights.get('narrative_hunter', 30),
            )
            breakdown['narrative_hunter'] = narrative_score
            reasoning_parts.append(f"Narrative: {narrative_reason}")
            if signals.narrative_volume_spike >= 3.0:
                primary_sources.append("narrative")

            # Rug Warden (PASS counts as primary source — enables convergence path)
            warden_score, warden_reason = self.score_rug_warden(
                signals.rug_warden_status,
                max_points=weights.get('rug_warden', 20),
            )
            breakdown['rug_warden'] = warden_score
            reasoning_parts.append(f"Warden: {warden_reason}")
            if signals.rug_warden_status == "PASS":
                primary_sources.append("warden")

            # Edge Bank
            if edge_bank_active:
                edge_score, edge_reason = self.score_edge_bank(
                    signals.edge_bank_match_pct,
                    max_points=weights.get('edge_bank', 10),
                )
                breakdown['edge_bank'] = edge_score
                reasoning_parts.append(f"Edge: {edge_reason}")
            else:
                breakdown['edge_bank'] = 0
                reasoning_parts.append(f"Edge: cold start (warden +{warden_bonus}pts)")

        # ORDERING SCORE: Pure signal strength
        ordering_score = sum(v for k, v in breakdown.items()
                            if not k.startswith('pulse_') or k == 'pulse_quality')

        # PERMISSION SCORE: Start with ordering, apply penalties
        permission_score = ordering_score

        # RED FLAG 1: Concentrated Volume (B1)
        if concentrated_volume:
            penalty = 15
            red_flags['concentrated_volume'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"RED FLAG: Concentrated volume (-{penalty} pts)")

        # RED FLAG 2: Dumper Wallets (B1)
        if dumper_wallet_count > 0:
            if dumper_wallet_count >= signals.smart_money_whales and signals.smart_money_whales > 0:
                return ConvictionScore(
                    ordering_score=ordering_score,
                    permission_score=0,
                    breakdown=breakdown,
                    red_flags=red_flags,
                    primary_sources=primary_sources,
                    recommendation="VETO",
                    position_size_sol=0.0,
                    reasoning=f"All {dumper_wallet_count} whale(s) are known dumpers — trade vetoed",
                    play_type=play_type,
                )
            else:
                if dumper_wallet_count == 1:
                    penalty = 15
                else:
                    penalty = 30
                red_flags['dumper_wallets'] = -penalty
                permission_score -= penalty
                reasoning_parts.append(f"RED FLAG: {dumper_wallet_count} dumper wallet(s) (-{penalty} pts)")

        # RED FLAG 3: Fresh Wallet Concentration (TGM)
        if signals.fresh_wallet_inflow_usd > 50000:
            penalty = 10
            red_flags['fresh_wallet_concentration'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"RED FLAG: Fresh wallet inflow ${signals.fresh_wallet_inflow_usd:,.0f} (-{penalty} pts)")

        # RED FLAG 4: Exchange Inflow / Distribution Pattern (TGM)
        if signals.exchange_outflow_usd > 0:
            penalty = 10
            red_flags['exchange_inflow'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"RED FLAG: Exchange inflow ${signals.exchange_outflow_usd:,.0f} — distribution pattern (-{penalty} pts)")

        # RED FLAG 4b: Unsocialized Volume Spike (reduced from -25 to -5)
        # With X API disabled, kol_detected is always false. The old -25 penalty
        # was killing every hot accumulation candidate. Reduced to mild warning —
        # Rug Warden is the real safety gate, not social confirmation.
        if (play_type == "accumulation"
                and signals.narrative_volume_spike >= 20.0
                and not signals.narrative_kol_detected):
            penalty = 5
            red_flags['unsocialized_volume'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(
                f"MILD FLAG: {signals.narrative_volume_spike:.0f}x volume spike "
                f"with no social confirmation (-{penalty} pts)"
            )

        # RED FLAG 5: S2 Divergence Damping (Oracle <-> Narrative mismatch)
        if (signals.smart_money_whales >= 2
                and signals.narrative_volume_spike < 2.0
                and not signals.narrative_kol_detected):
            penalty = 25
            red_flags['divergence_damping'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(
                f"S2 DAMPING: {signals.smart_money_whales} whales but no narrative "
                f"momentum (-{penalty} pts)"
            )

        # PULSE RED FLAGS (apply to both play types — scoring penalties, not pre-filters)
        if signals.pulse_organic_ratio < 0.3 and signals.pulse_organic_ratio > 0:
            penalty = 10
            red_flags['pulse_low_organic'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"PULSE RED FLAG: Organic ratio {signals.pulse_organic_ratio:.2f} (-{penalty} pts)")

        # BUNDLER PENALTY REMOVED: Data shows bundler-flagged trades have the
        # HIGHEST win rate (40.4%) and smallest losses (-2.0%). Bundlers on PumpFun
        # are often creators doing initial market-making — their presence correlates
        # with tokens that have a floor. Penalty was counterproductive.
        # if signals.pulse_bundler_pct > 20:
        #     penalty = 10
        #     red_flags['pulse_bundler'] = -penalty
        #     permission_score -= penalty

        if signals.pulse_sniper_pct > 30:
            penalty = 10
            red_flags['pulse_sniper'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"PULSE RED FLAG: Snipers {signals.pulse_sniper_pct:.1f}% (-{penalty} pts)")

        # pulse_serial_deployer: Now a hard VETO (see VETO 4 above)
        # Kept as comment — the >5 migrations check fires as VETO before reaching here.

        # FDV CAUTION ZONE: $25k-100k graduation plays have lower win rate (31.2%)
        # than $10k-25k sweet spot (40.2%), but flagged trades show +2.8% avg PnL.
        # Reduced from -15 to -5: original penalty overcorrected.
        if (play_type == "graduation"
                and signals.entry_market_cap_usd > 25000
                and signals.entry_market_cap_usd < 100000):
            penalty = 5
            red_flags['fdv_death_zone'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(
                f"FDV CAUTION: ${signals.entry_market_cap_usd:,.0f} "
                f"(graduation $25k-100k zone, -5 pts)"
            )

        # POST-BONDING: Now a hard VETO (see VETO 5 above).
        # Kept as comment — the bonded check fires as VETO before reaching here.

        # PULSE BONUSES for accumulation play type (graduation handles these in score_pulse_quality)
        if play_type == "accumulation":
            if signals.pulse_ghost_metadata:
                bonus = 5
                breakdown['pulse_ghost'] = bonus
                ordering_score += bonus
                permission_score += bonus
                reasoning_parts.append(f"PULSE BONUS: Ghost metadata (+{bonus} pts)")

            if signals.pulse_pro_trader_pct > 10:
                bonus = 5
                breakdown['pulse_pro_trader'] = bonus
                ordering_score += bonus
                permission_score += bonus
                reasoning_parts.append(f"PULSE BONUS: Pro traders {signals.pulse_pro_trader_pct:.1f}% (+{bonus} pts)")

            # PRIMARY SOURCE: Pulse (accumulation profile)
            if signals.pulse_pro_trader_pct > 10 and signals.pulse_organic_ratio >= 0.3:
                primary_sources.append("pulse")

        # ENRICHMENT BONUSES (apply to ALL play types — boost only, never penalize)
        if signals.holder_delta_pct > 20:
            bonus = 5
            breakdown['enrichment_holder_growth'] = bonus
            ordering_score += bonus
            permission_score += bonus
            reasoning_parts.append(f"ENRICHMENT: Rapid holder growth {signals.holder_delta_pct:.0f}% (+{bonus} pts)")

        if signals.pulse_trending_score > 100:
            bonus = 5 if signals.pulse_trending_score <= 1000 else 8
            breakdown['enrichment_trending'] = bonus
            ordering_score += bonus
            permission_score += bonus
            reasoning_parts.append(f"ENRICHMENT: Trending on Mobula score={signals.pulse_trending_score:.0f} (+{bonus} pts)")

        if signals.pulse_dexscreener_boosted:
            bonus = 5
            breakdown['enrichment_ds_boosted'] = bonus
            ordering_score += bonus
            permission_score += bonus
            reasoning_parts.append(f"ENRICHMENT: DexScreener boosted (+{bonus} pts)")

        # Apply data completeness penalty (Phase 2)
        permission_score = int(permission_score * data_completeness)
        if data_completeness < 1.0:
            reasoning_parts.append(f"Data completeness: {data_completeness:.1%}")

        # PERMISSION GATE (A1): Require >=2 PRIMARY sources for AUTO_EXECUTE
        # Graduation plays skip the 2-source gate — Pulse quality + Rug Warden PASS is enough.
        # Graduation is a speed play; requiring 2+ sources would block nearly everything.
        num_primary = len(primary_sources)
        auto_threshold = self._get_auto_execute_threshold(play_type)

        # Determine base recommendation
        if permission_score >= auto_threshold:
            if play_type == "graduation" or num_primary >= 2:
                recommendation = "AUTO_EXECUTE"
            else:
                recommendation = "WATCHLIST"
                reasoning_parts.append(f"PERMISSION GATE: Only {num_primary} primary source(s) — need >=2 for AUTO_EXECUTE")
        elif permission_score >= self.thresholds['watchlist']:
            recommendation = "WATCHLIST"
        elif permission_score >= self.thresholds.get('paper_trade', 40):
            recommendation = "PAPER_TRADE"
        else:
            recommendation = "DISCARD"

        # TIME MISMATCH DOWNGRADE (B2): Oracle accumulation + Narrative age <5min
        if time_mismatch:
            if recommendation == "AUTO_EXECUTE":
                recommendation = "WATCHLIST"
                reasoning_parts.append("TIME MISMATCH: Oracle + Narrative <5min -> downgraded to WATCHLIST")
            elif recommendation == "WATCHLIST":
                recommendation = "DISCARD"
                reasoning_parts.append("TIME MISMATCH: Oracle + Narrative <5min -> downgraded to DISCARD")

        # Calculate position size
        position_size = self.calculate_position_size(
            permission_score, pot_balance_sol, volatility_factor,
            play_type=play_type, sol_price_usd=sol_price_usd,
        )

        return ConvictionScore(
            ordering_score=ordering_score,
            permission_score=permission_score,
            breakdown=breakdown,
            red_flags=red_flags,
            primary_sources=primary_sources,
            recommendation=recommendation,
            position_size_sol=position_size,
            reasoning=" | ".join(reasoning_parts),
            play_type=play_type,
        )


def main():
    """CLI for testing conviction scoring."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Calculate conviction score")
    parser.add_argument("--whales", type=int, default=0, help="Number of whales accumulating")
    parser.add_argument("--volume-spike", type=float, default=0.0, help="Volume multiple vs avg")
    parser.add_argument("--kol", action="store_true", help="KOL detected")
    parser.add_argument("--narrative-age", type=int, default=0, help="Narrative age in minutes")
    parser.add_argument("--rug-warden", default="UNKNOWN", choices=["PASS", "WARN", "FAIL", "UNKNOWN"])
    parser.add_argument("--edge-match", type=float, default=0.0, help="Edge bank match %")
    parser.add_argument("--pot", type=float, required=True, help="Current pot balance in SOL")
    parser.add_argument("--volatility", type=float, default=1.0, help="Volatility factor")
    parser.add_argument("--concentrated-vol", action="store_true", help="Volume is concentrated")
    parser.add_argument("--dumpers", type=int, default=0, help="Number of dumper wallets")
    parser.add_argument("--time-mismatch", action="store_true", help="Oracle + Narrative <5min")
    parser.add_argument("--exchange-outflow", type=float, default=0.0, help="Exchange net flow USD (positive=inflow)")
    parser.add_argument("--fresh-wallet-inflow", type=float, default=0.0, help="Fresh wallet inflow USD")
    parser.add_argument("--sm-buy-volume", type=float, default=0.0, help="Smart money buy volume USD")
    parser.add_argument("--dca-count", type=int, default=0, help="Active smart money DCAs")
    # Pulse fields
    parser.add_argument("--pulse-ghost", action="store_true", help="Ghost metadata detected")
    parser.add_argument("--pulse-organic", type=float, default=1.0, help="Pulse organic ratio")
    parser.add_argument("--pulse-bundler", type=float, default=0.0, help="Pulse bundler %")
    parser.add_argument("--pulse-sniper", type=float, default=0.0, help="Pulse sniper %")
    parser.add_argument("--pulse-pro", type=float, default=0.0, help="Pulse pro trader %")
    parser.add_argument("--pulse-deployer", type=int, default=0, help="Deployer migrations")
    parser.add_argument("--pulse-stage", default="", help="Pulse stage: bonded or bonding")
    parser.add_argument("--holder-delta", type=float, default=0.0, help="Holder count change %")
    parser.add_argument("--entry-mcap", type=float, default=0.0, help="Entry market cap USD")
    parser.add_argument("--pulse-trending", type=float, default=0.0, help="Pulse trending score 1h")
    parser.add_argument("--pulse-ds-boosted", action="store_true", help="DexScreener boosted")

    args = parser.parse_args()

    signals = SignalInput(
        smart_money_whales=args.whales,
        narrative_volume_spike=args.volume_spike,
        narrative_kol_detected=args.kol,
        narrative_age_minutes=args.narrative_age,
        rug_warden_status=args.rug_warden,
        edge_bank_match_pct=args.edge_match,
        exchange_outflow_usd=args.exchange_outflow,
        fresh_wallet_inflow_usd=args.fresh_wallet_inflow,
        smart_money_buy_volume_usd=args.sm_buy_volume,
        dca_count=args.dca_count,
        pulse_ghost_metadata=args.pulse_ghost,
        pulse_organic_ratio=args.pulse_organic,
        pulse_bundler_pct=args.pulse_bundler,
        pulse_sniper_pct=args.pulse_sniper,
        pulse_pro_trader_pct=args.pulse_pro,
        pulse_deployer_migrations=args.pulse_deployer,
        pulse_stage=args.pulse_stage,
        holder_delta_pct=args.holder_delta,
        entry_market_cap_usd=args.entry_mcap,
        pulse_trending_score=args.pulse_trending,
        pulse_dexscreener_boosted=args.pulse_ds_boosted,
    )

    scorer = ConvictionScorer()
    result = scorer.score(
        signals,
        args.pot,
        args.volatility,
        concentrated_volume=args.concentrated_vol,
        dumper_wallet_count=args.dumpers,
        time_mismatch=args.time_mismatch,
    )

    output = {
        "play_type": result.play_type,
        "ordering_score": result.ordering_score,
        "permission_score": result.permission_score,
        "breakdown": result.breakdown,
        "red_flags": result.red_flags,
        "primary_sources": result.primary_sources,
        "recommendation": result.recommendation,
        "position_size_sol": round(result.position_size_sol, 4),
        "reasoning": result.reasoning,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
