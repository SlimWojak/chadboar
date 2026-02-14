#!/usr/bin/env python3
"""
Conviction Scoring System
Weighted signal aggregation for trade decision-making.
"""
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


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


class ConvictionScorer:
    """Calculate conviction scores from signal inputs."""
    
    def __init__(self, config_path: Path = Path("config/risk.yaml")):
        """Load scoring configuration."""
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.weights = self.config['conviction']['weights']
        self.thresholds = self.config['conviction']['thresholds']
        self.sizing = self.config['conviction']['sizing']
        self.portfolio = self.config['portfolio']
        self.trade_limits = self.config['trade']
    
    def score_smart_money_oracle(self, whales: int) -> tuple[int, str]:
        """Score whale accumulation signals."""
        if whales == 0:
            return 0, "No whale accumulation detected"
        
        # +15 per whale, cap at 40 (requires 3+ whales for max)
        score = min(whales * 15, self.weights['smart_money_oracle'])
        
        if whales >= 3:
            return score, f"{whales} distinct whales accumulating (max points)"
        else:
            return score, f"{whales} whale(s) detected (+15 each)"
    
    def score_narrative_hunter(
        self, 
        volume_spike: float, 
        kol_detected: bool, 
        age_minutes: int
    ) -> tuple[int, str]:
        """Score social momentum + volume signals."""
        max_points = self.weights['narrative_hunter']
        
        # No signal
        if volume_spike < 5.0 and not kol_detected:
            return 0, "No narrative momentum"
        
        # Base score from volume spike
        if volume_spike >= 5.0:
            # Scale: 5x = 15pts, 10x = 25pts, 20x+ = 30pts
            base = min(int((volume_spike / 5.0) * 15), 25)
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
        if volume_spike >= 5.0:
            reasoning_parts.append(f"{volume_spike:.1f}x volume spike")
        if kol_detected:
            reasoning_parts.append("KOL detected")
        if age_minutes > 30:
            reasoning_parts.append(f"decayed ({age_minutes}min old)")
        
        reasoning = ", ".join(reasoning_parts) if reasoning_parts else "No narrative signal"
        
        return score, reasoning
    
    def score_rug_warden(self, status: str) -> tuple[int, str]:
        """Score Rug Warden validation."""
        if status == "PASS":
            return self.weights['rug_warden'], "Rug Warden: PASS"
        elif status == "WARN":
            return int(self.weights['rug_warden'] * 0.5), "Rug Warden: WARN (partial points)"
        else:  # FAIL or UNKNOWN
            return 0, f"Rug Warden: {status}"
    
    def score_edge_bank(self, match_pct: float) -> tuple[int, str]:
        """Score historical pattern match."""
        max_points = self.weights['edge_bank']
        
        if match_pct < 70.0:
            return 0, "No strong historical match"
        
        # Linear scale from 70% (5pts) to 100% (10pts)
        score = int(((match_pct - 70) / 30) * max_points)
        score = min(score, max_points)
        
        return score, f"{match_pct:.0f}% match to past winners"
    
    def calculate_position_size(
        self, 
        score: int, 
        pot_balance_sol: float,
        volatility_factor: float = 1.0
    ) -> float:
        """Calculate position size based on conviction score."""
        # Formula: size = (score / 100) × (pot × 0.01) × (1 / volatility_factor)
        base_size = (score / 100) * (pot_balance_sol * self.sizing['base_multiplier'])
        adjusted_size = base_size / volatility_factor
        
        # Cap at max_position_pct
        max_size = pot_balance_sol * (self.trade_limits['max_position_pct'] / 100)
        return min(adjusted_size, max_size)
    
    def score(
        self, 
        signals: SignalInput,
        pot_balance_sol: float,
        volatility_factor: float = 1.0,
        data_completeness: float = 1.0,  # Phase 2: uncertainty multiplier
        concentrated_volume: bool = False,  # Phase 3: red flag
        dumper_wallet_count: int = 0,  # Phase 3: red flag
        time_mismatch: bool = False,  # Phase 4: disagreement flag
    ) -> ConvictionScore:
        """
        Calculate total conviction score and recommendation.
        
        Args:
            signals: Input signals from detectors
            pot_balance_sol: Current pot balance in SOL
            volatility_factor: Volatility adjustment (default 1.0)
            data_completeness: Multiplier for partial data penalty (0.0-1.0)
        
        Returns:
            ConvictionScore with ordering_score, permission_score, and recommendation
        """
        breakdown = {}
        red_flags = {}
        primary_sources = []
        reasoning_parts = []
        
        # VETO CHECKS (Expanded in Phase 6)
        
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
                reasoning="VETO: Rug Warden FAIL (INV-RUG-WARDEN-VETO)"
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
                reasoning="VETO: Token created <2min ago (too new for organic discovery)"
            )
        
        # VETO 4: Volume spike ≥10x with near-zero social
        if (signals.narrative_volume_spike >= 10.0 and 
            not signals.narrative_kol_detected):
            # Check if there's truly zero social presence
            # For now, KOL detection is our proxy for social activity
            # This veto triggers when there's massive volume but no KOL mentions
            return ConvictionScore(
                ordering_score=0,
                permission_score=0,
                breakdown={},
                red_flags={},
                primary_sources=[],
                recommendation="VETO",
                position_size_sol=0.0,
                reasoning=f"VETO: {signals.narrative_volume_spike:.0f}x volume spike with no social activity (wash trading)"
            )
        
        # VETO 5: Liquidity dropping during detection
        # This requires passing liquidity delta as a parameter
        # For now, this veto is handled in Rug Warden checks
        # TODO: Add liquidity_delta parameter in future enhancement
        
        # Score each signal
        oracle_score, oracle_reason = self.score_smart_money_oracle(signals.smart_money_whales)
        breakdown['smart_money_oracle'] = oracle_score
        reasoning_parts.append(f"Oracle: {oracle_reason}")
        
        # PRIMARY SOURCE 1: Oracle (≥3 whales)
        if signals.smart_money_whales >= 3:
            primary_sources.append("oracle")
        
        narrative_score, narrative_reason = self.score_narrative_hunter(
            signals.narrative_volume_spike,
            signals.narrative_kol_detected,
            signals.narrative_age_minutes
        )
        breakdown['narrative_hunter'] = narrative_score
        reasoning_parts.append(f"Narrative: {narrative_reason}")
        
        # PRIMARY SOURCE 2: Narrative (≥5x volume spike)
        if signals.narrative_volume_spike >= 5.0:
            primary_sources.append("narrative")
        
        warden_score, warden_reason = self.score_rug_warden(signals.rug_warden_status)
        breakdown['rug_warden'] = warden_score
        reasoning_parts.append(f"Warden: {warden_reason}")
        
        # PRIMARY SOURCE 3: Rug Warden (PASS or WARN)
        if signals.rug_warden_status in ["PASS", "WARN"]:
            primary_sources.append("warden")
        
        edge_score, edge_reason = self.score_edge_bank(signals.edge_bank_match_pct)
        breakdown['edge_bank'] = edge_score
        reasoning_parts.append(f"Edge: {edge_reason}")
        
        # ORDERING SCORE: Pure signal strength
        ordering_score = sum(breakdown.values())
        
        # PERMISSION SCORE: Start with ordering, apply penalties
        permission_score = ordering_score
        
        # RED FLAG 1: Concentrated Volume (B1)
        if concentrated_volume:
            penalty = 15
            red_flags['concentrated_volume'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"RED FLAG: Concentrated volume (−{penalty} pts)")
        
        # RED FLAG 2: Dumper Wallets (B1)
        if dumper_wallet_count > 0:
            if dumper_wallet_count >= signals.smart_money_whales and signals.smart_money_whales > 0:
                # ALL whales are dumpers → VETO
                return ConvictionScore(
                    ordering_score=ordering_score,
                    permission_score=0,
                    breakdown=breakdown,
                    red_flags=red_flags,
                    primary_sources=primary_sources,
                    recommendation="VETO",
                    position_size_sol=0.0,
                    reasoning=f"All {dumper_wallet_count} whale(s) are known dumpers — trade vetoed"
                )
            else:
                # Partial dumpers → gradient penalty
                if dumper_wallet_count == 1:
                    penalty = 15
                else:  # 2+
                    penalty = 30
                red_flags['dumper_wallets'] = -penalty
                permission_score -= penalty
                reasoning_parts.append(f"RED FLAG: {dumper_wallet_count} dumper wallet(s) (−{penalty} pts)")
        
        # RED FLAG 3: Fresh Wallet Concentration (TGM)
        if signals.fresh_wallet_inflow_usd > 50000:
            penalty = 10
            red_flags['fresh_wallet_concentration'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"RED FLAG: Fresh wallet inflow ${signals.fresh_wallet_inflow_usd:,.0f} (−{penalty} pts)")

        # RED FLAG 4: Exchange Inflow / Distribution Pattern (TGM)
        if signals.exchange_outflow_usd > 0:
            penalty = 10
            red_flags['exchange_inflow'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"RED FLAG: Exchange inflow ${signals.exchange_outflow_usd:,.0f} — distribution pattern (−{penalty} pts)")

        # RED FLAG 5: S2 Divergence Damping (Oracle ↔ Narrative mismatch)
        # Whales accumulating but zero narrative momentum → suspicious
        # accumulation without organic discovery.
        if (signals.smart_money_whales >= 2
                and signals.narrative_volume_spike < 2.0
                and not signals.narrative_kol_detected):
            penalty = 25
            red_flags['divergence_damping'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(
                f"S2 DAMPING: {signals.smart_money_whales} whales but no narrative "
                f"momentum (−{penalty} pts)"
            )

        # PULSE SCORING (Phase 0 — Mobula Pulse bonding/bonded signals)

        # PULSE BONUS 1: Ghost metadata (stealth launch, no socials but volume)
        if signals.pulse_ghost_metadata:
            bonus = 5
            breakdown['pulse_ghost'] = bonus
            ordering_score += bonus
            permission_score += bonus
            reasoning_parts.append(f"PULSE BONUS: Ghost metadata (+{bonus} pts)")

        # PULSE BONUS 2: Pro traders > 10% holdings
        if signals.pulse_pro_trader_pct > 10:
            bonus = 5
            breakdown['pulse_pro_trader'] = bonus
            ordering_score += bonus
            permission_score += bonus
            reasoning_parts.append(f"PULSE BONUS: Pro traders {signals.pulse_pro_trader_pct:.1f}% (+{bonus} pts)")

        # PULSE RED FLAG 1: Low organic volume ratio (< 0.3 = bot/fake volume)
        if signals.pulse_organic_ratio < 0.3 and signals.pulse_organic_ratio > 0:
            penalty = 10
            red_flags['pulse_low_organic'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"PULSE RED FLAG: Organic ratio {signals.pulse_organic_ratio:.2f} (−{penalty} pts)")

        # PULSE RED FLAG 2: High bundler holdings (> 20%)
        if signals.pulse_bundler_pct > 20:
            penalty = 10
            red_flags['pulse_bundler'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"PULSE RED FLAG: Bundlers {signals.pulse_bundler_pct:.1f}% (−{penalty} pts)")

        # PULSE RED FLAG 3: High sniper holdings (> 30%)
        if signals.pulse_sniper_pct > 30:
            penalty = 10
            red_flags['pulse_sniper'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"PULSE RED FLAG: Snipers {signals.pulse_sniper_pct:.1f}% (−{penalty} pts)")

        # PULSE RED FLAG 4: Serial deployer (> 3 migrations = rug risk)
        if signals.pulse_deployer_migrations > 3:
            penalty = 10
            red_flags['pulse_serial_deployer'] = -penalty
            permission_score -= penalty
            reasoning_parts.append(f"PULSE RED FLAG: Deployer {signals.pulse_deployer_migrations} migrations (−{penalty} pts)")

        # PRIMARY SOURCE: Pulse (bonded + pro_trader > 10%)
        if signals.pulse_pro_trader_pct > 10 and signals.pulse_organic_ratio >= 0.3:
            primary_sources.append("pulse")

        # Apply data completeness penalty (Phase 2)
        permission_score = int(permission_score * data_completeness)
        if data_completeness < 1.0:
            reasoning_parts.append(f"Data completeness: {data_completeness:.1%}")
        
        # PERMISSION GATE (A1): Require ≥2 PRIMARY sources for AUTO_EXECUTE
        num_primary = len(primary_sources)
        
        # Determine base recommendation
        if permission_score >= self.thresholds['auto_execute']:
            # CONSTITUTIONAL GATE: AUTO_EXECUTE requires ≥2 PRIMARY sources
            if num_primary >= 2:
                recommendation = "AUTO_EXECUTE"
            else:
                recommendation = "WATCHLIST"
                reasoning_parts.append(f"PERMISSION GATE: Only {num_primary} primary source(s) — need ≥2 for AUTO_EXECUTE")
        elif permission_score >= self.thresholds['watchlist']:
            recommendation = "WATCHLIST"
        else:
            recommendation = "DISCARD"
        
        # TIME MISMATCH DOWNGRADE (B2): Oracle accumulation + Narrative age <5min
        if time_mismatch:
            if recommendation == "AUTO_EXECUTE":
                recommendation = "WATCHLIST"
                reasoning_parts.append("TIME MISMATCH: Oracle + Narrative <5min → downgraded to WATCHLIST")
            elif recommendation == "WATCHLIST":
                recommendation = "DISCARD"
                reasoning_parts.append("TIME MISMATCH: Oracle + Narrative <5min → downgraded to DISCARD")
        
        # Calculate position size (use permission_score, not ordering)
        position_size = self.calculate_position_size(permission_score, pot_balance_sol, volatility_factor)
        
        return ConvictionScore(
            ordering_score=ordering_score,
            permission_score=permission_score,
            breakdown=breakdown,
            red_flags=red_flags,
            primary_sources=primary_sources,
            recommendation=recommendation,
            position_size_sol=position_size,
            reasoning=" | ".join(reasoning_parts)
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
        "ordering_score": result.ordering_score,
        "permission_score": result.permission_score,
        "breakdown": result.breakdown,
        "red_flags": result.red_flags,
        "primary_sources": result.primary_sources,
        "recommendation": result.recommendation,
        "position_size_sol": round(result.position_size_sol, 4),
        "reasoning": result.reasoning
    }
    
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
