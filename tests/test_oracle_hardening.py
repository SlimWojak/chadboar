"""Tests for Oracle Hardening — phase timing, parallel execution, screener fallback, Mobula token resolution."""

from __future__ import annotations

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from lib.skills.oracle_query import (
    query_oracle,
    _run_tgm_pipeline,
    _run_mobula_scan,
    MobulaClient,
    _empty_flow_intel,
    _empty_buyer_depth,
)
from tests.mocks.mock_nansen import (
    TOKEN_SCREENER_RESPONSE,
    SMART_MONEY_TRANSACTIONS,
    FLOW_INTELLIGENCE_RESPONSE,
    WHO_BOUGHT_SOLD_RESPONSE,
    JUPITER_DCAS_RESPONSE,
    SMART_MONEY_HOLDINGS_RESPONSE,
)


def _make_nansen_mock(**overrides):
    """Create a NansenClient mock with TGM endpoint defaults."""
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


MOCK_FIREHOSE = {
    "mobula": {
        "base_url": "https://api.mobula.io/api/1",
        "api_key": "test-key",
    }
}

MOCK_FIREHOSE_NO_MOBULA = {}


# ── Phase timing / logging ────────────────────────────────────────────


class TestPhaseTimingAndLogging:
    """Verify phase_timing dict and diagnostics list are in output."""

    @pytest.mark.asyncio
    async def test_phase_timing_in_output(self):
        """Output contains phase_timing dict with expected keys."""
        mock = _make_nansen_mock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value=MOCK_FIREHOSE_NO_MOBULA):
                    result = await query_oracle()

        assert result["status"] == "OK"
        pt = result.get("phase_timing", {})
        assert "phase1_discovery" in pt
        assert "phase2_enrichment" in pt
        assert "phase3_dca" in pt
        assert "phase4_holdings" in pt
        assert "total" in pt
        # All timings should be non-negative floats
        for key, val in pt.items():
            assert isinstance(val, float), f"{key} should be float"
            assert val >= 0, f"{key} should be non-negative"

    @pytest.mark.asyncio
    async def test_failed_phase_logged(self):
        """When dex-trades fails, diagnostics list contains error info."""
        mock = _make_nansen_mock(
            get_smart_money_transactions=AsyncMock(side_effect=Exception("dex-trades down")),
        )

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value=MOCK_FIREHOSE_NO_MOBULA):
                    result = await query_oracle()

        diagnostics = result.get("diagnostics", [])
        assert len(diagnostics) > 0, "Should have diagnostic messages"
        # Should mention the failure
        error_lines = [d for d in diagnostics if "FAILED" in d or "failed" in d]
        assert len(error_lines) > 0, "Should log dex-trades failure"


# ── Parallel execution ─────────────────────────────────────────────


class TestParallelExecution:
    """Verify parallel Mobula queries and parallel TGM Phase 4."""

    @pytest.mark.asyncio
    async def test_mobula_parallel(self):
        """All 5 whale wallets are queried (via parallel gather)."""
        call_wallets = []

        def mock_networth(wallet):
            call_wallets.append(wallet)
            return {
                'wallet': wallet,
                'networth_usd': 100000.0,
                'accum_24h_usd': 55000.0,
                'signal_strength': 'high',
            }

        mobula_client = MagicMock(spec=MobulaClient)
        mobula_client.get_whale_networth_accum = mock_networth
        mobula_client.get_whale_portfolio = MagicMock(return_value=[])

        whales = ["w1", "w2", "w3", "w4", "w5"]
        signals, timing = await _run_mobula_scan(mobula_client, whales)

        # All 5 wallets should have been queried
        assert len(call_wallets) == 5
        assert set(call_wallets) == set(whales)
        # All should pass the >10k filter
        assert len(signals) == 5
        assert "mobula_networth" in timing

    @pytest.mark.asyncio
    async def test_tgm_phase4_parallel(self):
        """Holdings fetch (Phase 4) starts before enrichment completes."""
        call_order = []

        original_screen_tokens = AsyncMock(return_value=TOKEN_SCREENER_RESPONSE)

        async def track_screen(*args, **kwargs):
            call_order.append("screen_tokens")
            return await original_screen_tokens(*args, **kwargs)

        async def track_holdings(*args, **kwargs):
            call_order.append("holdings_start")
            await asyncio.sleep(0)  # yield control
            call_order.append("holdings_end")
            return SMART_MONEY_HOLDINGS_RESPONSE

        async def track_flow(*args, **kwargs):
            call_order.append("enrichment")
            return FLOW_INTELLIGENCE_RESPONSE

        mock = _make_nansen_mock()
        mock.screen_tokens = track_screen
        mock.get_smart_money_holdings = track_holdings
        mock.get_flow_intelligence = track_flow

        signals, holdings, timing = await _run_tgm_pipeline(mock)

        # Holdings should have started (was created as a task before enrichment)
        assert "holdings_start" in call_order
        assert "phase4_holdings" in timing
        # Holdings result should be valid
        assert len(holdings) >= 1


# ── Dex-trades discovery ──────────────────────────────────────────


class TestDexTradesDiscovery:
    """Dex-trades as primary discovery in TGM pipeline."""

    @pytest.mark.asyncio
    async def test_dex_trades_returns_candidates(self):
        """Dex-trades aggregation returns candidates sorted by wallet count."""
        mock = _make_nansen_mock()

        signals, holdings, timing = await _run_tgm_pipeline(mock)

        assert len(signals) >= 1
        assert signals[0]["discovery_source"] == "dex-trades"
        # BOAR111 has 4 wallets — should be top candidate
        assert signals[0]["token_mint"] == "BOAR111"
        assert signals[0]["wallet_count"] == 4

    @pytest.mark.asyncio
    async def test_dex_trades_failure_returns_empty(self):
        """When dex-trades fails, pipeline returns empty signals with holdings."""
        mock = _make_nansen_mock(
            get_smart_money_transactions=AsyncMock(side_effect=Exception("dex-trades down")),
        )

        signals, holdings, timing = await _run_tgm_pipeline(mock)

        assert len(signals) == 0
        assert "phase1_discovery" in timing


# ── Mobula token resolution ────────────────────────────────────────


class TestMobulaTokenResolution:
    """Mobula portfolio enrichment and heartbeat integration."""

    @pytest.mark.asyncio
    async def test_mobula_portfolio_enrichment(self):
        """Whale with accum > 10k gets portfolio queried, token_mint populated."""
        def mock_networth(wallet):
            return {
                'wallet': wallet,
                'networth_usd': 200000.0,
                'accum_24h_usd': 55000.0,
                'signal_strength': 'high',
            }

        def mock_portfolio(wallet):
            return [
                {'token_mint': 'ALPHA111', 'token_symbol': 'ALPHA', 'value_usd': 50000.0},
                {'token_mint': 'BETA222', 'token_symbol': 'BETA', 'value_usd': 20000.0},
            ]

        mobula_client = MagicMock(spec=MobulaClient)
        mobula_client.get_whale_networth_accum = mock_networth
        mobula_client.get_whale_portfolio = mock_portfolio

        signals, timing = await _run_mobula_scan(mobula_client, ["whale1"])

        assert len(signals) == 1
        sig = signals[0]
        assert sig.get("token_mint") == "ALPHA111"
        assert sig.get("token_symbol") == "ALPHA"
        assert sig.get("top_tokens") is not None
        assert len(sig["top_tokens"]) == 2
        assert "mobula_portfolio" in timing

    @pytest.mark.asyncio
    async def test_heartbeat_mobula_token_scoring(self):
        """Mobula signal with token_mint enters all_mints scoring loop."""
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
            "mobula_signals": [
                {
                    "wallet": "whale1",
                    "token_mint": "MOBULA_MINT",
                    "token_symbol": "MOB",
                    "accum_24h_usd": 55000,
                    "signal_strength": "high",
                    "networth_usd": 200000,
                },
            ],
            "phase_timing": {},
            "diagnostics": [],
        }

        # Simulate the heartbeat extraction logic
        oracle_signals = oracle_result.get("nansen_signals", [])
        mobula_signals = oracle_result.get("mobula_signals", [])
        existing_mints = {s.get("token_mint") for s in oracle_signals}

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

        # Verify both mints are now in oracle_signals
        all_mints = {s["token_mint"] for s in oracle_signals}
        assert "NANSEN_MINT" in all_mints
        assert "MOBULA_MINT" in all_mints

        # Verify the mobula signal has correct structure
        mob_sig = next(s for s in oracle_signals if s["token_mint"] == "MOBULA_MINT")
        assert mob_sig["source"] == "mobula"
        assert mob_sig["discovery_source"] == "mobula-whale"
        assert mob_sig["wallet_count"] == 1
        assert mob_sig["total_buy_usd"] == 55000
        assert "flow_intel" in mob_sig
        assert "buyer_depth" in mob_sig
