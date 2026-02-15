"""Tests for Mobula Pulse integration — Phase 0 bonding/bonded token detection.

Covers: Pulse API parsing, candidate filtering, parallel execution,
scoring bonuses/red flags, and heartbeat extraction.
"""

from __future__ import annotations

import asyncio
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
    "data": {
        "bonded": [
            {
                "address": "PULSE_BONDED_1",
                "symbol": "PBOND",
                "liquidity": 25000,
                "volume": 15000,
                "organicVolume": 12000,
                "bundlersHoldingsPercentage": 5.0,
                "snipersHoldingsPercentage": 8.0,
                "proTradersHoldingsPercentage": 15.0,
                "smartTradersHoldingsPercentage": 3.0,
                "deployerMigrations": 1,
                # No socials = ghost metadata
            },
            {
                "address": "PULSE_BONDED_2",
                "symbol": "PGOOD",
                "liquidity": 50000,
                "volume": 30000,
                "organicVolume": 28000,
                "bundlersHoldingsPercentage": 2.0,
                "snipersHoldingsPercentage": 5.0,
                "proTradersHoldingsPercentage": 20.0,
                "smartTradersHoldingsPercentage": 5.0,
                "twitter": "https://twitter.com/pgood",
                "deployerMigrations": 0,
            },
        ],
        "bonding": [
            {
                "address": "PULSE_BONDING_1",
                "symbol": "PCURVE",
                "liquidity": 8000,
                "volume": 3000,
                "organicVolume": 2500,
                "bundlersHoldingsPercentage": 3.0,
                "snipersHoldingsPercentage": 10.0,
                "proTradersHoldingsPercentage": 8.0,
                "smartTradersHoldingsPercentage": 2.0,
                "deployerMigrations": 0,
            },
        ],
    }
}

PULSE_RESPONSE_BAD_TOKENS = {
    "data": {
        "bonded": [
            {
                "address": "BUNDLER_COIN",
                "symbol": "BUND",
                "liquidity": 20000,
                "volume": 10000,
                "organicVolume": 9000,
                "bundlersHoldingsPercentage": 35.0,  # > 20% = reject
                "snipersHoldingsPercentage": 5.0,
                "proTradersHoldingsPercentage": 5.0,
                "smartTradersHoldingsPercentage": 0.0,
                "deployerMigrations": 0,
            },
            {
                "address": "SNIPER_COIN",
                "symbol": "SNIP",
                "liquidity": 20000,
                "volume": 10000,
                "organicVolume": 9000,
                "bundlersHoldingsPercentage": 5.0,
                "snipersHoldingsPercentage": 45.0,  # > 30% = reject
                "proTradersHoldingsPercentage": 5.0,
                "smartTradersHoldingsPercentage": 0.0,
                "deployerMigrations": 0,
            },
            {
                "address": "BOT_COIN",
                "symbol": "BOT",
                "liquidity": 20000,
                "volume": 10000,
                "organicVolume": 1000,  # organic ratio 0.1 < 0.3 = reject
                "bundlersHoldingsPercentage": 5.0,
                "snipersHoldingsPercentage": 5.0,
                "proTradersHoldingsPercentage": 5.0,
                "smartTradersHoldingsPercentage": 0.0,
                "deployerMigrations": 0,
            },
            {
                "address": "LOW_LIQ",
                "symbol": "LOWL",
                "liquidity": 2000,  # < 5000 = reject
                "volume": 10000,
                "organicVolume": 9000,
                "bundlersHoldingsPercentage": 5.0,
                "snipersHoldingsPercentage": 5.0,
                "proTradersHoldingsPercentage": 5.0,
                "smartTradersHoldingsPercentage": 0.0,
                "deployerMigrations": 0,
            },
        ],
        "bonding": [],
    }
}

PULSE_RESPONSE_EMPTY = {"data": {"bonded": [], "bonding": [], "new": []}}


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
        """Pulse scan returns empty on API failure."""
        mobula_client = MagicMock(spec=MobulaClient)
        mobula_client.get_pulse_listings = MagicMock(side_effect=Exception("API down"))

        signals, timing = await _run_pulse_scan(
            mobula_client, "https://pulse-v2-api.mobula.io"
        )

        assert signals == []
        assert "pulse_fetch" in timing


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
        # Pulse should be empty (failed gracefully)
        assert result["pulse_signals"] == []


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
        """Deployer with > 3 migrations triggers -10 penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            pulse_deployer_migrations=5,
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

        # Pulse extraction
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
                    "pulse_ghost_metadata": ps.get("pulse_ghost_metadata", False),
                    "pulse_organic_ratio": ps.get("pulse_organic_ratio", 1.0),
                    "pulse_bundler_pct": ps.get("pulse_bundler_pct", 0.0),
                    "pulse_sniper_pct": ps.get("pulse_sniper_pct", 0.0),
                    "pulse_pro_trader_pct": ps.get("pulse_pro_trader_pct", 0.0),
                    "pulse_deployer_migrations": ps.get("pulse_deployer_migrations", 0),
                })
                existing_mints.add(ps["token_mint"])

        # Verify both mints in scoring loop
        all_mints = {s["token_mint"] for s in oracle_signals}
        assert "NANSEN_MINT" in all_mints
        assert "PULSE_MINT" in all_mints

        # Verify pulse signal preserved fields
        pulse_sig = next(s for s in oracle_signals if s["token_mint"] == "PULSE_MINT")
        assert pulse_sig["source"] == "pulse"
        assert pulse_sig["discovery_source"] == "pulse-bonded"
        assert pulse_sig["pulse_ghost_metadata"] is True
        assert pulse_sig["pulse_organic_ratio"] == 0.85
        assert pulse_sig["pulse_pro_trader_pct"] == 18.0

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
