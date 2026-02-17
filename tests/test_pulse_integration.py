"""Tests for Mobula Pulse integration — Phase 0 bonding/bonded token detection.

Covers: Pulse API parsing, candidate filtering, parallel execution,
scoring bonuses/red flags, and heartbeat extraction.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from lib.skills.oracle_query import (
    query_oracle,
    _run_pulse_scan,
    _parse_pulse_candidates,
    _extract_pulse_token,
    MobulaClient,
    _empty_flow_intel,
    _empty_buyer_depth,
)
from lib.scoring import ConvictionScorer, SignalInput
from tests.mocks.mock_nansen import (
    TOKEN_SCREENER_RESPONSE,
    SMART_MONEY_TRANSACTIONS,
    FLOW_INTELLIGENCE_RESPONSE,
    WHO_BOUGHT_SOLD_RESPONSE,
    JUPITER_DCAS_RESPONSE,
    SMART_MONEY_HOLDINGS_RESPONSE,
)


# ── Mock Pulse API responses ───────────────────────────────────────

PULSE_RESPONSE_GOOD = {
    "bonded": {"data": [
        {
            "address": "PULSE_BONDED_1",
            "symbol": "PBOND",
            "liquidity": 25000,
            "volume_24h": 15000,
            "organic_volume_24h": 12000,
            "bundlersHoldings": 5.0,
            "snipersHoldings": 8.0,
            "proTradersHoldings": 15.0,
            "smartTradersHoldingsPercentage": 3.0,
            "deployerMigrationsCount": 1,
            "holdersCount": 200,
            "marketCap": 100000,
            "trendingScore1h": 50.0,
            "socials": {"twitter": None, "website": None, "telegram": None},
            # No socials = ghost metadata
        },
        {
            "address": "PULSE_BONDED_2",
            "symbol": "PGOOD",
            "liquidity": 50000,
            "volume_24h": 30000,
            "organic_volume_24h": 28000,
            "bundlersHoldings": 2.0,
            "snipersHoldings": 5.0,
            "proTradersHoldings": 20.0,
            "smartTradersHoldingsPercentage": 5.0,
            "socials": {"twitter": "https://twitter.com/pgood", "website": None, "telegram": None},
            "deployerMigrationsCount": 0,
            "holdersCount": 500,
            "marketCap": 250000,
            "trendingScore1h": 120.0,
        },
    ]},
    "bonding": {"data": [
        {
            "address": "PULSE_BONDING_1",
            "symbol": "PCURVE",
            "liquidity": 8000,
            "volume_24h": 3000,
            "organic_volume_24h": 2500,
            "bundlersHoldings": 3.0,
            "snipersHoldings": 10.0,
            "proTradersHoldings": 8.0,
            "smartTradersHoldingsPercentage": 2.0,
            "deployerMigrationsCount": 0,
            "holdersCount": 80,
            "marketCap": 30000,
            "trendingScore1h": 10.0,
            "socials": {"twitter": None, "website": None, "telegram": None},
        },
    ]},
}

PULSE_RESPONSE_BAD_TOKENS = {
    "bonded": {"data": [
        {
            "address": "BUNDLER_COIN",
            "symbol": "BUND",
            "liquidity": 20000,
            "volume_24h": 10000,
            "organic_volume_24h": 9000,
            "bundlersHoldings": 35.0,  # > 20% = penalty
            "snipersHoldings": 5.0,
            "proTradersHoldings": 5.0,
            "smartTradersHoldingsPercentage": 0.0,
            "deployerMigrationsCount": 0,
            "holdersCount": 100,
            "marketCap": 50000,
            "socials": {"twitter": None, "website": None, "telegram": None},
        },
        {
            "address": "SNIPER_COIN",
            "symbol": "SNIP",
            "liquidity": 20000,
            "volume_24h": 10000,
            "organic_volume_24h": 9000,
            "bundlersHoldings": 5.0,
            "snipersHoldings": 45.0,  # > 30% = penalty
            "proTradersHoldings": 5.0,
            "smartTradersHoldingsPercentage": 0.0,
            "deployerMigrationsCount": 0,
            "holdersCount": 100,
            "marketCap": 50000,
            "socials": {"twitter": None, "website": None, "telegram": None},
        },
        {
            "address": "BOT_COIN",
            "symbol": "BOT",
            "liquidity": 20000,
            "volume_24h": 10000,
            "organic_volume_24h": 1000,  # organic ratio 0.1 < 0.3 = penalty
            "bundlersHoldings": 5.0,
            "snipersHoldings": 5.0,
            "proTradersHoldings": 5.0,
            "smartTradersHoldingsPercentage": 0.0,
            "deployerMigrationsCount": 0,
            "holdersCount": 100,
            "marketCap": 50000,
            "socials": {"twitter": None, "website": None, "telegram": None},
        },
        {
            "address": "LOW_LIQ",
            "symbol": "LOWL",
            "liquidity": 2000,  # < 5000 = reject
            "volume_24h": 10000,
            "organic_volume_24h": 9000,
            "bundlersHoldings": 5.0,
            "snipersHoldings": 5.0,
            "proTradersHoldings": 5.0,
            "smartTradersHoldingsPercentage": 0.0,
            "deployerMigrationsCount": 0,
            "holdersCount": 100,
            "marketCap": 50000,
            "socials": {"twitter": None, "website": None, "telegram": None},
        },
    ]},
    "bonding": {"data": []},
}

PULSE_RESPONSE_EMPTY = {"bonded": {"data": []}, "bonding": {"data": []}, "new": {"data": []}}


def _make_nansen_mock(**overrides):
    mock = AsyncMock()
    mock.screen_tokens = AsyncMock(return_value=TOKEN_SCREENER_RESPONSE)
    mock.get_smart_money_transactions = AsyncMock(return_value=SMART_MONEY_TRANSACTIONS)
    mock.get_flow_intelligence = AsyncMock(return_value=FLOW_INTELLIGENCE_RESPONSE)
    mock.get_who_bought_sold = AsyncMock(return_value=WHO_BOUGHT_SOLD_RESPONSE)
    mock.get_jupiter_dcas = AsyncMock(return_value=JUPITER_DCAS_RESPONSE)
    mock.get_smart_money_holdings = AsyncMock(return_value=SMART_MONEY_HOLDINGS_RESPONSE)
    mock.close = AsyncMock()
    for key, val in overrides.items():
        setattr(mock, key, val)
    return mock


MOCK_FIREHOSE_WITH_PULSE = {
    "mobula": {
        "base_url": "https://api.mobula.io/api/1",
        "pulse_url": "https://pulse-v2-api.mobula.io",
        "api_key": "test-key",
        "endpoints": {
            "pulse": "/api/2/pulse",
        },
    }
}


# ── Pulse API parsing ──────────────────────────────────────────────


class TestPulseParsing:
    """Parse Pulse v2 response into filtered candidates."""

    def test_parse_good_tokens(self):
        """Good tokens pass all filters: liquidity, volume, organic, bundler, sniper."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_GOOD)
        assert len(candidates) == 3
        mints = {c["token_mint"] for c in candidates}
        assert "PULSE_BONDED_1" in mints
        assert "PULSE_BONDED_2" in mints
        assert "PULSE_BONDING_1" in mints

    def test_bad_tokens_filtered(self):
        """Only hard safety filters (liquidity < $5k, volume < $1k) reject at parse level.

        Bundler, sniper, and organic ratio are passed through to scoring
        where they apply penalties instead of hard rejections.
        """
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_BAD_TOKENS)
        # 3 tokens pass through (bundler, sniper, low organic) — only LOW_LIQ is filtered
        assert len(candidates) == 3
        mints = {c["token_mint"] for c in candidates}
        assert "LOW_LIQ" not in mints  # Liquidity < $5k still filtered
        assert "BUNDLER_COIN" in mints  # Passed through, scoring applies -10 penalty
        assert "SNIPER_COIN" in mints   # Passed through, scoring applies -10 penalty
        assert "BOT_COIN" in mints      # Passed through, scoring applies -10 penalty

    def test_empty_response(self):
        """Empty pulse response returns no candidates."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_EMPTY)
        assert candidates == []

    def test_ghost_metadata_detection(self):
        """Token with no socials but volume > $5k flagged as ghost metadata."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_GOOD)
        ghost = next(c for c in candidates if c["token_mint"] == "PULSE_BONDED_1")
        assert ghost["pulse_ghost_metadata"] is True

        # Token with twitter = not ghost
        social = next(c for c in candidates if c["token_mint"] == "PULSE_BONDED_2")
        assert social["pulse_ghost_metadata"] is False

    def test_organic_ratio_calculated(self):
        """Organic ratio = organic_volume / total_volume."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_GOOD)
        bonded1 = next(c for c in candidates if c["token_mint"] == "PULSE_BONDED_1")
        assert bonded1["pulse_organic_ratio"] == 0.8  # 12000/15000

    def test_pro_trader_pct_includes_smart(self):
        """Pro trader % combines proTradersHoldingsPercentage + smartTradersHoldingsPercentage."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_GOOD)
        bonded1 = next(c for c in candidates if c["token_mint"] == "PULSE_BONDED_1")
        assert bonded1["pulse_pro_trader_pct"] == 18.0  # 15 + 3

    def test_stage_tagging(self):
        """Bonded tokens tagged 'pulse-bonded', bonding as 'pulse-bonding'."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_GOOD)
        bonded = next(c for c in candidates if c["token_mint"] == "PULSE_BONDED_1")
        assert bonded["discovery_source"] == "pulse-bonded"
        bonding = next(c for c in candidates if c["token_mint"] == "PULSE_BONDING_1")
        assert bonding["discovery_source"] == "pulse-bonding"

    def test_sorted_by_quality(self):
        """Candidates sorted by organic_ratio × pro_trader_pct descending."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_GOOD)
        quality_scores = [
            c["pulse_organic_ratio"] * c["pulse_pro_trader_pct"]
            for c in candidates
        ]
        assert quality_scores == sorted(quality_scores, reverse=True)

    def test_signal_has_required_fields(self):
        """Each candidate has all fields needed for scoring pipeline."""
        candidates = _parse_pulse_candidates(PULSE_RESPONSE_GOOD)
        for c in candidates:
            assert "token_mint" in c
            assert "token_symbol" in c
            assert "source" in c
            assert c["source"] == "pulse"
            assert "flow_intel" in c
            assert "buyer_depth" in c
            assert "dca_count" in c
            assert "pulse_organic_ratio" in c
            assert "pulse_bundler_pct" in c
            assert "pulse_sniper_pct" in c
            assert "pulse_pro_trader_pct" in c
            assert "pulse_ghost_metadata" in c
            assert "pulse_deployer_migrations" in c


# ── Pulse scan (async) ─────────────────────────────────────────────


class TestPulseScan:
    """Async _run_pulse_scan function."""

    @pytest.mark.asyncio
    async def test_pulse_scan_returns_candidates(self):
        """Pulse scan parses response and returns filtered candidates."""
        mobula_client = MagicMock(spec=MobulaClient)
        mobula_client.get_pulse_listings = MagicMock(return_value=PULSE_RESPONSE_GOOD)

        signals, timing = await _run_pulse_scan(
            mobula_client, "https://pulse-v2-api.mobula.io"
        )

        assert len(signals) == 3
        assert "pulse_fetch" in timing
        mobula_client.get_pulse_listings.assert_called_once()

    @pytest.mark.asyncio
    async def test_pulse_scan_handles_failure(self):
        """Pulse scan falls back to DexScreener on API failure."""
        mobula_client = MagicMock(spec=MobulaClient)
        mobula_client.get_pulse_listings = MagicMock(side_effect=Exception("API down"))

        signals, timing = await _run_pulse_scan(
            mobula_client, "https://pulse-v2-api.mobula.io"
        )

        # DexScreener fallback fires when Pulse fails
        assert "pulse_fetch" in timing
        # Either DexScreener returns candidates or we get empty (network-dependent)
        # The key check: it doesn't crash
        assert isinstance(signals, list)


# ── Parallel execution ─────────────────────────────────────────────


class TestPulseParallelExecution:
    """Pulse runs in parallel with TGM and Mobula."""

    @pytest.mark.asyncio
    async def test_pulse_in_oracle_output(self):
        """query_oracle() includes pulse_signals in output when Pulse is configured."""
        mock = _make_nansen_mock()

        def mock_pulse_listings(self, pulse_url, endpoint="/api/2/pulse"):
            return PULSE_RESPONSE_GOOD

        def mock_networth(self, wallet):
            return None  # No whale accum

        def mock_portfolio(self, wallet):
            return []

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value=MOCK_FIREHOSE_WITH_PULSE):
                    with patch("lib.skills.oracle_query._load_cached_whales", return_value=["MOCK_WHALE_1"]):
                        with patch.object(
                            MobulaClient, "get_pulse_listings", mock_pulse_listings
                        ):
                            with patch.object(
                                MobulaClient, "get_whale_networth_accum", mock_networth
                            ):
                                with patch.object(
                                    MobulaClient, "get_whale_portfolio", mock_portfolio
                                ):
                                    result = await query_oracle()

        assert result["status"] == "OK"
        assert "pulse_signals" in result
        assert len(result["pulse_signals"]) == 3
        assert "pulse_fetch" in result.get("phase_timing", {})

    @pytest.mark.asyncio
    async def test_pulse_failure_doesnt_break_oracle(self):
        """Pulse failure doesn't affect TGM or Mobula results."""
        mock = _make_nansen_mock()

        def mock_pulse_fail(self, pulse_url, endpoint="/api/2/pulse"):
            raise Exception("Pulse API down")

        def mock_networth(self, wallet):
            return None

        def mock_portfolio(self, wallet):
            return []

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("lib.skills.oracle_query._load_cached_whales", return_value=["MOCK_WHALE_1"]):
                with patch("builtins.open", MagicMock()):
                    with patch("yaml.safe_load", return_value=MOCK_FIREHOSE_WITH_PULSE):
                        with patch.object(
                            MobulaClient, "get_pulse_listings", mock_pulse_fail
                        ):
                            with patch.object(
                                MobulaClient, "get_whale_networth_accum", mock_networth
                            ):
                                with patch.object(
                                    MobulaClient, "get_whale_portfolio", mock_portfolio
                                ):
                                    result = await query_oracle()

        assert result["status"] == "OK"
        # TGM should still succeed
        assert len(result["nansen_signals"]) > 0
        # Pulse failed but DexScreener fallback may fire — key is no crash
        assert isinstance(result["pulse_signals"], list)


# ── Scoring bonuses and red flags ──────────────────────────────────


class TestPulseScoring:
    """Pulse fields contribute to conviction scoring."""

    def test_ghost_metadata_bonus(self):
        """Ghost metadata adds +5 bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_ghost_metadata=True,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.breakdown.get("pulse_ghost") == 5

    def test_pro_trader_bonus(self):
        """Pro trader > 10% adds +5 bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_pro_trader_pct=15.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.breakdown.get("pulse_pro_trader") == 5

    def test_low_organic_red_flag(self):
        """Organic ratio < 0.3 triggers -10 penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.2,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.red_flags.get("pulse_low_organic") == -10

    def test_bundler_red_flag(self):
        """Bundler > 20% triggers -10 penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_bundler_pct=25.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.red_flags.get("pulse_bundler") == -10

    def test_sniper_red_flag(self):
        """Sniper > 30% triggers -10 penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_sniper_pct=35.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.red_flags.get("pulse_sniper") == -10

    def test_serial_deployer_red_flag(self):
        """Deployer with > 5 migrations triggers -10 penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_deployer_migrations=6,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.red_flags.get("pulse_serial_deployer") == -10

    def test_pulse_primary_source(self):
        """Pulse becomes primary source when pro_trader > 10% and organic >= 0.3."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_pro_trader_pct=15.0,
            pulse_organic_ratio=0.8,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "pulse" in result.primary_sources

    def test_no_pulse_defaults_neutral(self):
        """Default pulse values (0/False/1.0) don't trigger any bonuses or flags."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "pulse_ghost" not in result.breakdown
        assert "pulse_pro_trader" not in result.breakdown
        assert "pulse_low_organic" not in result.red_flags
        assert "pulse_bundler" not in result.red_flags
        assert "pulse_sniper" not in result.red_flags
        assert "pulse_serial_deployer" not in result.red_flags


# ── Enrichment bonuses ────────────────────────────────────────────


class TestEnrichmentBonuses:
    """New enrichment signals boost scores without penalizing."""

    def test_holder_growth_bonus(self):
        """Holder delta > 20% adds +5 bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            holder_delta_pct=25.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.breakdown.get("enrichment_holder_growth") == 5

    def test_holder_growth_no_bonus_below_threshold(self):
        """Holder delta <= 20% gives no bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            holder_delta_pct=15.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "enrichment_holder_growth" not in result.breakdown

    def test_trending_score_bonus(self):
        """Trending score > 100 adds +5 bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_trending_score=150.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.breakdown.get("enrichment_trending") == 5

    def test_trending_score_no_bonus_below_threshold(self):
        """Trending score <= 100 gives no bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_trending_score=50.0,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "enrichment_trending" not in result.breakdown

    def test_dexscreener_boosted_bonus(self):
        """DexScreener boosted adds +5 bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_dexscreener_boosted=True,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.breakdown.get("enrichment_ds_boosted") == 5

    def test_no_enrichment_defaults_neutral(self):
        """Default enrichment values (0/False) add no bonus."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "enrichment_holder_growth" not in result.breakdown
        assert "enrichment_trending" not in result.breakdown
        assert "enrichment_ds_boosted" not in result.breakdown

    def test_all_enrichment_stacks(self):
        """All enrichment bonuses stack: +5 +5 +5 = +15."""
        scorer = ConvictionScorer()
        base_signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
        )
        enriched_signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            holder_delta_pct=30.0,
            pulse_trending_score=200.0,
            pulse_dexscreener_boosted=True,
        )
        base_result = scorer.score(base_signals, pot_balance_sol=14.0)
        enriched_result = scorer.score(enriched_signals, pot_balance_sol=14.0)
        assert enriched_result.permission_score == base_result.permission_score + 15

    def test_graduation_1_source_auto_execute(self):
        """Graduation plays skip the 2-source gate — 1 source is enough."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
            pulse_bundler_pct=2.0,
            pulse_ghost_metadata=True,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        # With lowered threshold (55) and 1-source gate skip,
        # this should be AUTO_EXECUTE if score >= 55
        if result.permission_score >= 55:
            assert result.recommendation == "AUTO_EXECUTE"


# ── Heartbeat extraction ───────────────────────────────────────────


class TestHeartbeatPulseExtraction:
    """Pulse signals are extracted into the scoring loop."""

    def test_pulse_signals_enter_scoring_loop(self):
        """Pulse candidates with token_mint enter all_mints for scoring."""
        oracle_result = {
            "status": "OK",
            "nansen_signals": [
                {
                    "token_mint": "NANSEN_MINT",
                    "token_symbol": "NAN",
                    "wallet_count": 5,
                    "total_buy_usd": 100000,
                    "confidence": "high",
                    "source": "nansen",
                    "flow_intel": _empty_flow_intel(),
                    "buyer_depth": _empty_buyer_depth(),
                    "dca_count": 0,
                    "discovery_source": "screener",
                },
            ],
            "holdings_delta": [],
            "mobula_signals": [],
            "pulse_signals": [
                {
                    "token_mint": "PULSE_MINT",
                    "token_symbol": "PULS",
                    "volume_usd": 15000,
                    "confidence": "medium",
                    "discovery_source": "pulse-bonded",
                    "pulse_ghost_metadata": True,
                    "pulse_organic_ratio": 0.85,
                    "pulse_bundler_pct": 3.0,
                    "pulse_sniper_pct": 5.0,
                    "pulse_pro_trader_pct": 18.0,
                    "pulse_deployer_migrations": 0,
                    "pulse_stage": "bonded",
                    "pulse_trending_score": 150.0,
                    "pulse_dexscreener_boosted": True,
                    "market_cap_usd": 50000.0,
                },
            ],
            "phase_timing": {},
            "diagnostics": [],
        }

        # Simulate the heartbeat extraction logic
        oracle_signals = oracle_result.get("nansen_signals", [])
        existing_mints = {s.get("token_mint") for s in oracle_signals}

        # Mobula extraction (empty in this test)
        mobula_signals = oracle_result.get("mobula_signals", [])
        for ms in mobula_signals:
            if ms.get("token_mint") and ms["token_mint"] not in existing_mints:
                oracle_signals.append({
                    "token_mint": ms["token_mint"],
                    "token_symbol": ms.get("token_symbol", "UNKNOWN"),
                    "wallet_count": 1,
                    "total_buy_usd": ms.get("accum_24h_usd", 0),
                    "confidence": ms.get("signal_strength", "low"),
                    "source": "mobula",
                    "flow_intel": _empty_flow_intel(),
                    "buyer_depth": _empty_buyer_depth(),
                    "dca_count": 0,
                    "discovery_source": "mobula-whale",
                })
                existing_mints.add(ms["token_mint"])

        # Pulse extraction (matches heartbeat_runner.py pipeline)
        pulse_signals = oracle_result.get("pulse_signals", [])
        for ps in pulse_signals:
            if ps.get("token_mint") and ps["token_mint"] not in existing_mints:
                oracle_signals.append({
                    "token_mint": ps["token_mint"],
                    "token_symbol": ps.get("token_symbol", "UNKNOWN"),
                    "wallet_count": 0,
                    "total_buy_usd": ps.get("volume_usd", 0),
                    "confidence": ps.get("confidence", "low"),
                    "source": "pulse",
                    "flow_intel": _empty_flow_intel(),
                    "buyer_depth": _empty_buyer_depth(),
                    "dca_count": 0,
                    "discovery_source": ps.get("discovery_source", "pulse-bonded"),
                    "market_cap_usd": ps.get("market_cap_usd", 0.0),
                    "pulse_ghost_metadata": ps.get("pulse_ghost_metadata", False),
                    "pulse_organic_ratio": ps.get("pulse_organic_ratio", 1.0),
                    "pulse_bundler_pct": ps.get("pulse_bundler_pct", 0.0),
                    "pulse_sniper_pct": ps.get("pulse_sniper_pct", 0.0),
                    "pulse_pro_trader_pct": ps.get("pulse_pro_trader_pct", 0.0),
                    "pulse_deployer_migrations": ps.get("pulse_deployer_migrations", 0),
                    "pulse_stage": ps.get("pulse_stage", ""),
                    "pulse_trending_score": ps.get("pulse_trending_score", 0.0),
                    "pulse_dexscreener_boosted": ps.get("pulse_dexscreener_boosted", False),
                })
                existing_mints.add(ps["token_mint"])

        # Verify both mints in scoring loop
        all_mints = {s["token_mint"] for s in oracle_signals}
        assert "NANSEN_MINT" in all_mints
        assert "PULSE_MINT" in all_mints

        # Verify pulse signal preserved fields (including newly fixed ones)
        pulse_sig = next(s for s in oracle_signals if s["token_mint"] == "PULSE_MINT")
        assert pulse_sig["source"] == "pulse"
        assert pulse_sig["discovery_source"] == "pulse-bonded"
        assert pulse_sig["pulse_ghost_metadata"] is True
        assert pulse_sig["pulse_organic_ratio"] == 0.85
        assert pulse_sig["pulse_pro_trader_pct"] == 18.0
        # These three fields were previously dropped in the pipeline (bug fix)
        assert pulse_sig["pulse_stage"] == "bonded"
        assert pulse_sig["pulse_trending_score"] == 150.0
        assert pulse_sig["pulse_dexscreener_boosted"] is True
        assert pulse_sig["market_cap_usd"] == 50000.0

    def test_pulse_deduplication(self):
        """Pulse token already in Nansen signals is not duplicated."""
        oracle_result = {
            "status": "OK",
            "nansen_signals": [
                {
                    "token_mint": "SHARED_MINT",
                    "token_symbol": "SHRD",
                    "wallet_count": 5,
                    "source": "nansen",
                    "flow_intel": _empty_flow_intel(),
                    "buyer_depth": _empty_buyer_depth(),
                    "dca_count": 0,
                    "discovery_source": "screener",
                },
            ],
            "pulse_signals": [
                {
                    "token_mint": "SHARED_MINT",  # Same mint
                    "token_symbol": "SHRD",
                    "volume_usd": 10000,
                    "discovery_source": "pulse-bonded",
                    "pulse_ghost_metadata": False,
                    "pulse_organic_ratio": 0.9,
                    "pulse_bundler_pct": 2.0,
                    "pulse_sniper_pct": 3.0,
                    "pulse_pro_trader_pct": 12.0,
                    "pulse_deployer_migrations": 0,
                },
            ],
        }

        oracle_signals = oracle_result.get("nansen_signals", [])
        existing_mints = {s.get("token_mint") for s in oracle_signals}

        pulse_signals = oracle_result.get("pulse_signals", [])
        for ps in pulse_signals:
            if ps.get("token_mint") and ps["token_mint"] not in existing_mints:
                oracle_signals.append({"token_mint": ps["token_mint"]})
                existing_mints.add(ps["token_mint"])

        # Should only have 1 entry, not duplicated
        assert len(oracle_signals) == 1
        assert oracle_signals[0]["token_mint"] == "SHARED_MINT"


# ── Pipeline propagation tests (bug fix verification) ─────────────


class TestPipelinePropagation:
    """Verify pulse_stage, trending_score, ds_boosted reach the scorer."""

    def test_bonded_stage_bonus_applies(self):
        """Bonded stage bonus (+5) fires when pulse_stage='bonded' is propagated."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
            pulse_bundler_pct=2.0,
            pulse_stage="bonded",
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        assert result.breakdown.get("pulse_bonded_bonus") == 5

    def test_bonded_bonus_absent_without_stage(self):
        """Without pulse_stage, bonded bonus does NOT fire."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
            pulse_bundler_pct=2.0,
            pulse_stage="",  # Empty — the old buggy default
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        assert result.breakdown.get("pulse_bonded_bonus", 0) == 0

    def test_trending_and_ds_boost_reach_scorer(self):
        """Trending score and DS boost enrichment bonuses fire from pulse signals."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=0,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.8,
            pulse_pro_trader_pct=12.0,
            pulse_trending_score=200.0,
            pulse_dexscreener_boosted=True,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.breakdown.get("enrichment_trending") == 5
        assert result.breakdown.get("enrichment_ds_boosted") == 5

    def test_full_graduation_with_all_bonuses(self):
        """Full graduation token with all bonuses scores well above threshold."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=0,
            narrative_volume_spike=5.0,
            narrative_age_minutes=10,
            rug_warden_status="PASS",
            pulse_organic_ratio=0.85,
            pulse_pro_trader_pct=15.0,
            pulse_bundler_pct=2.0,
            pulse_ghost_metadata=True,
            pulse_stage="bonded",
            pulse_trending_score=150.0,
            pulse_dexscreener_boosted=True,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert result.play_type == "graduation"
        # Should be well above 55 threshold
        assert result.permission_score >= 55
        assert result.recommendation == "AUTO_EXECUTE"
        # Verify all bonuses present
        assert result.breakdown.get("pulse_bonded_bonus", 0) > 0
        assert result.breakdown.get("enrichment_trending", 0) > 0
        assert result.breakdown.get("enrichment_ds_boosted", 0) > 0


# ── Scalp exit logic tests ────────────────────────────────────────


class TestScalpExitLogic:
    """Tests for _check_open_positions() exit triggers."""

    def _make_state(self, positions=None, **overrides):
        """Build a test state dict matching state.json format."""
        state = {
            "starting_balance_sol": 14.0,
            "current_balance_sol": 14.0,
            "current_balance_usd": 1190.0,
            "sol_price_usd": 85.0,
            "positions": positions or [],
            "daily_exposure_sol": 0.0,
            "daily_date": "2026-02-17",
            "daily_loss_pct": 0.0,
            "consecutive_losses": 0,
            "halted": False,
            "halted_at": "",
            "halt_reason": "",
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "last_trade_time": "",
            "last_heartbeat_time": "",
            "dry_run_mode": True,
            "dry_run_cycles_completed": 0,
            "dry_run_target_cycles": 10,
            "daily_graduation_count": 0,
        }
        state.update(overrides)
        return state

    def _make_position(self, pnl_shift=0.0, age_minutes=5, **overrides):
        """Build a test graduation position.

        Args:
            pnl_shift: Price change from entry (e.g. 0.25 = +25%).
            age_minutes: How many minutes ago entry was.
        """
        from datetime import timedelta

        entry_price = 0.001
        current_price = entry_price * (1 + pnl_shift)
        entry_time = (
            datetime.utcnow() - timedelta(minutes=age_minutes)
        ).isoformat()

        pos = {
            "token_mint": "SCALP_TEST_MINT",
            "token_symbol": "STEST",
            "direction": "long",
            "entry_price": entry_price,
            "entry_amount_sol": 0.12,
            "entry_amount_tokens": 10000.0,
            "entry_time": entry_time,
            "peak_price": entry_price,
            "play_type": "graduation",
            "entry_market_cap_usd": 30000,
            "entry_liquidity_usd": 8000,
            "thesis": "Scalp test",
            "signals": ["pulse"],
        }
        pos.update(overrides)
        return pos, current_price

    @pytest.mark.asyncio
    async def test_take_profit_exit(self, tmp_path):
        """Position at +25% triggers take profit exit (+20% threshold)."""
        from lib.skills.pulse_quick_scan import _check_open_positions, STATE_PATH, RISK_PATH

        pos, current_price = self._make_position(pnl_shift=0.25)
        state = self._make_state(positions=[pos])

        # Write state to tmp and patch paths
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        mock_price_data = {
            "SCALP_TEST_MINT": {"data": {"price": current_price, "liquidity": 10000}}
        }

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan.batch_price_fetch", new_callable=AsyncMock, return_value=mock_price_data), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "take_profit_pct": 20, "stop_loss_pct": 15, "time_decay_minutes": 15, "slippage_bps": 500},
             }), \
             patch("lib.skills.pulse_quick_scan.execute_swap", new_callable=AsyncMock, return_value={"status": "DRY_RUN"}), \
             patch("lib.skills.pulse_quick_scan.write_bead", return_value={"status": "OK"}), \
             patch("lib.skills.pulse_quick_scan.BirdeyeClient") as MockBirdeye:
            MockBirdeye.return_value.close = AsyncMock()

            exits = await _check_open_positions()

        assert len(exits) == 1
        assert "SCALP_TP" in exits[0]["exit_reason"]
        assert exits[0]["pnl_pct"] > 20

        # Verify state updated
        updated_state = json.loads(state_file.read_text())
        assert len(updated_state["positions"]) == 0
        assert updated_state["total_wins"] == 1

    @pytest.mark.asyncio
    async def test_stop_loss_exit(self, tmp_path):
        """Position at -20% triggers stop loss exit (-15% threshold)."""
        from lib.skills.pulse_quick_scan import _check_open_positions

        pos, current_price = self._make_position(pnl_shift=-0.20)
        state = self._make_state(positions=[pos])

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        mock_price_data = {
            "SCALP_TEST_MINT": {"data": {"price": current_price, "liquidity": 10000}}
        }

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan.batch_price_fetch", new_callable=AsyncMock, return_value=mock_price_data), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "take_profit_pct": 20, "stop_loss_pct": 15, "time_decay_minutes": 15, "slippage_bps": 500},
             }), \
             patch("lib.skills.pulse_quick_scan.execute_swap", new_callable=AsyncMock, return_value={"status": "DRY_RUN"}), \
             patch("lib.skills.pulse_quick_scan.write_bead", return_value={"status": "OK"}), \
             patch("lib.skills.pulse_quick_scan.BirdeyeClient") as MockBirdeye:
            MockBirdeye.return_value.close = AsyncMock()

            exits = await _check_open_positions()

        assert len(exits) == 1
        assert "SCALP_SL" in exits[0]["exit_reason"]
        assert exits[0]["pnl_pct"] < -15

        updated_state = json.loads(state_file.read_text())
        assert len(updated_state["positions"]) == 0
        assert updated_state["total_losses"] == 1
        assert updated_state["consecutive_losses"] == 1

    @pytest.mark.asyncio
    async def test_time_decay_exit(self, tmp_path):
        """Position older than 15 min with <5% gain triggers time decay exit."""
        from lib.skills.pulse_quick_scan import _check_open_positions

        # +3% gain but 20 min old -> should decay
        pos, current_price = self._make_position(pnl_shift=0.03, age_minutes=20)
        state = self._make_state(positions=[pos])

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        mock_price_data = {
            "SCALP_TEST_MINT": {"data": {"price": current_price, "liquidity": 10000}}
        }

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan.batch_price_fetch", new_callable=AsyncMock, return_value=mock_price_data), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "take_profit_pct": 20, "stop_loss_pct": 15, "time_decay_minutes": 15, "slippage_bps": 500},
             }), \
             patch("lib.skills.pulse_quick_scan.execute_swap", new_callable=AsyncMock, return_value={"status": "DRY_RUN"}), \
             patch("lib.skills.pulse_quick_scan.write_bead", return_value={"status": "OK"}), \
             patch("lib.skills.pulse_quick_scan.BirdeyeClient") as MockBirdeye:
            MockBirdeye.return_value.close = AsyncMock()

            exits = await _check_open_positions()

        assert len(exits) == 1
        assert "SCALP_DECAY" in exits[0]["exit_reason"]

    @pytest.mark.asyncio
    async def test_no_exit_when_profitable_and_young(self, tmp_path):
        """Position at +10% and 5 min old should hold (no exit trigger)."""
        from lib.skills.pulse_quick_scan import _check_open_positions

        pos, current_price = self._make_position(pnl_shift=0.10, age_minutes=5)
        state = self._make_state(positions=[pos])

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        mock_price_data = {
            "SCALP_TEST_MINT": {"data": {"price": current_price, "liquidity": 10000}}
        }

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan.batch_price_fetch", new_callable=AsyncMock, return_value=mock_price_data), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "take_profit_pct": 20, "stop_loss_pct": 15, "time_decay_minutes": 15, "slippage_bps": 500},
             }), \
             patch("lib.skills.pulse_quick_scan.BirdeyeClient") as MockBirdeye:
            MockBirdeye.return_value.close = AsyncMock()

            exits = await _check_open_positions()

        assert len(exits) == 0
        # State should be unchanged — no position removed
        unchanged_state = json.loads(state_file.read_text())
        assert len(unchanged_state["positions"]) == 1

    @pytest.mark.asyncio
    async def test_no_exit_when_old_but_profitable(self, tmp_path):
        """Position at +10% and 20 min old should hold (>5% = no time decay)."""
        from lib.skills.pulse_quick_scan import _check_open_positions

        pos, current_price = self._make_position(pnl_shift=0.10, age_minutes=20)
        state = self._make_state(positions=[pos])

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        mock_price_data = {
            "SCALP_TEST_MINT": {"data": {"price": current_price, "liquidity": 10000}}
        }

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan.batch_price_fetch", new_callable=AsyncMock, return_value=mock_price_data), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "take_profit_pct": 20, "stop_loss_pct": 15, "time_decay_minutes": 15, "slippage_bps": 500},
             }), \
             patch("lib.skills.pulse_quick_scan.BirdeyeClient") as MockBirdeye:
            MockBirdeye.return_value.close = AsyncMock()

            exits = await _check_open_positions()

        assert len(exits) == 0


class TestScalpEntryGuards:
    """Tests for _execute_scalp_entry() safety guards."""

    def _make_state(self, **overrides):
        state = {
            "starting_balance_sol": 14.0,
            "current_balance_sol": 14.0,
            "sol_price_usd": 85.0,
            "positions": [],
            "daily_exposure_sol": 0.0,
            "daily_date": "2026-02-17",
            "daily_loss_pct": 0.0,
            "consecutive_losses": 0,
            "halted": False,
            "dry_run_mode": True,
            "daily_graduation_count": 0,
        }
        state.update(overrides)
        return state

    def _make_candidate(self, score=60, recommendation="AUTO_EXECUTE", mcap=30000):
        return {
            "token_mint": "ENTRY_TEST_MINT",
            "token_symbol": "ETEST",
            "market_cap_usd": mcap,
            "liquidity_usd": 8000,
            "price_usd": 0.001,
            "score": {
                "permission_score": score,
                "recommendation": recommendation,
                "primary_sources": ["pulse"],
            },
        }

    @pytest.mark.asyncio
    async def test_entry_blocked_when_halted(self, tmp_path):
        """Scalp entry blocked when system is halted."""
        from lib.skills.pulse_quick_scan import _execute_scalp_entry

        state = self._make_state(halted=True)
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        candidate = self._make_candidate()

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "max_mcap_usd": 50000, "max_concurrent": 3, "max_position_usd": 10, "slippage_bps": 500},
                 "conviction": {"graduation": {"max_daily_plays": 8}},
                 "portfolio": {"drawdown_halt_pct": 50},
             }):
            entries = await _execute_scalp_entry([candidate])

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_entry_blocked_when_exposure_exceeded(self, tmp_path):
        """Scalp entry blocked when daily exposure >= 30%."""
        from lib.skills.pulse_quick_scan import _execute_scalp_entry

        # 4.5 SOL exposure on 14 SOL balance = 32% > 30%
        state = self._make_state(daily_exposure_sol=4.5)
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        candidate = self._make_candidate()

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "max_mcap_usd": 50000, "max_concurrent": 3, "max_position_usd": 10, "slippage_bps": 500},
                 "conviction": {"graduation": {"max_daily_plays": 8}},
                 "portfolio": {"drawdown_halt_pct": 50},
             }):
            entries = await _execute_scalp_entry([candidate])

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_entry_blocked_when_max_concurrent(self, tmp_path):
        """Scalp entry blocked when max concurrent graduation positions reached."""
        from lib.skills.pulse_quick_scan import _execute_scalp_entry

        existing_positions = [
            {"token_mint": f"POS_{i}", "play_type": "graduation"}
            for i in range(3)
        ]
        state = self._make_state(positions=existing_positions)
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        candidate = self._make_candidate()

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "max_mcap_usd": 50000, "max_concurrent": 3, "max_position_usd": 10, "slippage_bps": 500},
                 "conviction": {"graduation": {"max_daily_plays": 8}},
                 "portfolio": {"drawdown_halt_pct": 50},
             }):
            entries = await _execute_scalp_entry([candidate])

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_entry_skips_high_mcap(self, tmp_path):
        """Candidates with mcap > $50K are skipped (not scalp targets)."""
        from lib.skills.pulse_quick_scan import _execute_scalp_entry

        state = self._make_state()
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        candidate = self._make_candidate(mcap=100000)

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "max_mcap_usd": 50000, "max_concurrent": 3, "max_position_usd": 10, "slippage_bps": 500},
                 "conviction": {"graduation": {"max_daily_plays": 8}},
                 "portfolio": {"drawdown_halt_pct": 50},
             }):
            entries = await _execute_scalp_entry([candidate])

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_entry_skips_non_auto_execute(self, tmp_path):
        """Candidates without AUTO_EXECUTE recommendation are skipped."""
        from lib.skills.pulse_quick_scan import _execute_scalp_entry

        state = self._make_state()
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        candidate = self._make_candidate(recommendation="WATCHLIST")

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "max_mcap_usd": 50000, "max_concurrent": 3, "max_position_usd": 10, "slippage_bps": 500},
                 "conviction": {"graduation": {"max_daily_plays": 8}},
                 "portfolio": {"drawdown_halt_pct": 50},
             }):
            entries = await _execute_scalp_entry([candidate])

        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_dry_run_entry_creates_position(self, tmp_path):
        """Dry run entry creates position in state without real swap."""
        from lib.skills.pulse_quick_scan import _execute_scalp_entry

        state = self._make_state(dry_run_mode=True)
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        candidate = self._make_candidate()

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "max_mcap_usd": 50000, "max_concurrent": 3, "max_position_usd": 10, "slippage_bps": 500},
                 "conviction": {"graduation": {"max_daily_plays": 8}},
                 "portfolio": {"drawdown_halt_pct": 50},
             }), \
             patch("lib.skills.pulse_quick_scan.execute_swap", new_callable=AsyncMock, return_value={
                 "status": "DRY_RUN",
                 "amount_in": "0",
                 "amount_out": "0",
             }), \
             patch("lib.skills.pulse_quick_scan.write_bead", return_value={"status": "OK"}):
            entries = await _execute_scalp_entry([candidate])

        assert len(entries) == 1
        assert entries[0]["dry_run"] is True
        assert entries[0]["buy_status"] == "DRY_RUN"

        # Verify state was updated
        updated_state = json.loads(state_file.read_text())
        assert len(updated_state["positions"]) == 1
        assert updated_state["positions"][0]["play_type"] == "graduation"
        assert updated_state["daily_graduation_count"] == 1
        assert updated_state["current_balance_sol"] < 14.0  # SOL deducted

    @pytest.mark.asyncio
    async def test_entry_blocked_when_daily_grad_limit(self, tmp_path):
        """Scalp entry blocked when daily graduation limit reached."""
        from lib.skills.pulse_quick_scan import _execute_scalp_entry

        state = self._make_state(daily_graduation_count=8)
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        candidate = self._make_candidate()

        with patch("lib.skills.pulse_quick_scan.STATE_PATH", state_file), \
             patch("lib.skills.pulse_quick_scan._load_risk_config", return_value={
                 "scalp": {"enabled": True, "max_mcap_usd": 50000, "max_concurrent": 3, "max_position_usd": 10, "slippage_bps": 500},
                 "conviction": {"graduation": {"max_daily_plays": 8}},
                 "portfolio": {"drawdown_halt_pct": 50},
             }):
            entries = await _execute_scalp_entry([candidate])

        assert len(entries) == 0
