"""Tests for Smart Money Oracle.

Validates TGM pipeline, screener discovery, fallback to dex-trades,
flow intelligence parsing, red flags, and enriched output format.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from lib.skills.oracle_query import (
    query_oracle,
    _parse_screener_candidates,
    _parse_dex_trades_candidates,
    _parse_holdings_delta,
    _fetch_flow_intel,
    _fetch_buyer_depth,
    _fetch_dca_count,
    _run_tgm_pipeline,
    _enrich_signals,
)
from lib.scoring import ConvictionScorer, SignalInput
from tests.mocks.mock_nansen import (
    SMART_MONEY_TRANSACTIONS,
    TOKEN_SMART_MONEY,
    TOKEN_SCREENER_RESPONSE,
    FLOW_INTELLIGENCE_RESPONSE,
    FLOW_INTELLIGENCE_EXCHANGE_INFLOW,
    FLOW_INTELLIGENCE_FRESH_WALLET,
    WHO_BOUGHT_SOLD_RESPONSE,
    JUPITER_DCAS_RESPONSE,
    JUPITER_DCAS_EMPTY,
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
    mock.get_token_smart_money = AsyncMock(return_value=TOKEN_SMART_MONEY)
    mock.close = AsyncMock()
    for key, val in overrides.items():
        setattr(mock, key, val)
    return mock


class TestScreenerDiscovery:
    """Token Screener as primary discovery engine."""

    @pytest.mark.asyncio
    async def test_screener_returns_candidates(self):
        """Screener data parses into candidates sorted by wallet count."""
        candidates = _parse_screener_candidates(TOKEN_SCREENER_RESPONSE)
        assert len(candidates) == 3
        assert candidates[0]["token_mint"] == "ALPHA111"
        assert candidates[0]["wallet_count"] == 7
        assert candidates[0]["confidence"] == "high"
        assert candidates[1]["token_mint"] == "BETA222"
        assert candidates[1]["wallet_count"] == 4

    @pytest.mark.asyncio
    async def test_dex_trades_discovery_with_enrichment(self):
        """Full pipeline: dex-trades → flow intel + who bought → DCAs → holdings."""
        mock = _make_nansen_mock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value={}):
                    result = await query_oracle()

        assert result["status"] == "OK"
        signals = result["nansen_signals"]
        assert len(signals) == 3

        # Check enrichment happened on top candidate (BOAR = 4 wallets from dex-trades)
        boar = signals[0]
        assert boar["token_mint"] == "BOAR111"
        assert "flow_intel" in boar
        assert boar["flow_intel"]["whale_net_usd"] == 120000
        assert "buyer_depth" in boar
        assert boar["buyer_depth"]["smart_money_buyers"] == 5
        assert boar["discovery_source"] == "dex-trades"

        # Holdings delta should be present
        assert len(result["holdings_delta"]) >= 1

    @pytest.mark.asyncio
    async def test_screener_fallback_to_dex_trades(self):
        """When screener fails, falls back to dex-trades."""
        mock = _make_nansen_mock(
            screen_tokens=AsyncMock(side_effect=Exception("screener down")),
        )

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value={}):
                    result = await query_oracle()

        assert result["status"] == "OK"
        signals = result["nansen_signals"]
        # Should have found BOAR from dex-trades (4 wallets >= 3 threshold)
        assert len(signals) >= 1
        boar = next((s for s in signals if s["token_mint"] == "BOAR111"), None)
        assert boar is not None
        assert boar["wallet_count"] == 4
        assert boar["discovery_source"] == "dex-trades"


class TestFlowIntelParsing:
    """Flow Intelligence data parsing and interpretation."""

    @pytest.mark.asyncio
    async def test_flow_intel_dict_format(self):
        """Parse dict-style flow intelligence response."""
        mock = AsyncMock()
        mock.get_flow_intelligence = AsyncMock(return_value=FLOW_INTELLIGENCE_RESPONSE)
        result = await _fetch_flow_intel(mock, "ALPHA111")
        assert result["smart_trader_net_usd"] == 45000
        assert result["whale_net_usd"] == 120000
        assert result["exchange_net_usd"] == -35000
        assert result["fresh_wallet_net_usd"] == 8000
        assert result["top_pnl_net_usd"] == 22000

    @pytest.mark.asyncio
    async def test_flow_intel_list_format(self):
        """Parse list-style flow intelligence response."""
        list_response = {
            "data": [
                {"label": "Smart Trader", "net_usd": 30000},
                {"label": "Whale", "net_usd": 90000},
                {"label": "Exchange", "net_usd": -20000},
                {"label": "Fresh Wallet", "net_usd": 5000},
                {"label": "Top PnL", "net_usd": 12000},
            ]
        }
        mock = AsyncMock()
        mock.get_flow_intelligence = AsyncMock(return_value=list_response)
        result = await _fetch_flow_intel(mock, "ALPHA111")
        assert result["smart_trader_net_usd"] == 30000
        assert result["whale_net_usd"] == 90000
        assert result["exchange_net_usd"] == -20000

    @pytest.mark.asyncio
    async def test_buyer_depth_dict_format(self):
        """Parse dict-style who bought/sold response."""
        mock = AsyncMock()
        mock.get_who_bought_sold = AsyncMock(return_value=WHO_BOUGHT_SOLD_RESPONSE)
        result = await _fetch_buyer_depth(mock, "ALPHA111")
        assert result["smart_money_buyers"] == 5
        assert result["total_buy_volume_usd"] == 142000
        assert result["smart_money_sellers"] == 1
        assert result["total_sell_volume_usd"] == 18000

    @pytest.mark.asyncio
    async def test_buyer_depth_list_format(self):
        """Parse list-style who bought/sold response."""
        list_response = {
            "data": [
                {"side": "buy", "is_smart_money": True, "volume_usd": 50000},
                {"side": "buy", "is_smart_money": True, "volume_usd": 30000},
                {"side": "buy", "is_smart_money": False, "volume_usd": 10000},
                {"side": "sell", "is_smart_money": True, "volume_usd": 8000},
            ]
        }
        mock = AsyncMock()
        mock.get_who_bought_sold = AsyncMock(return_value=list_response)
        result = await _fetch_buyer_depth(mock, "ALPHA111")
        assert result["smart_money_buyers"] == 2
        assert result["total_buy_volume_usd"] == 90000
        assert result["smart_money_sellers"] == 1
        assert result["total_sell_volume_usd"] == 8000


class TestExchangeOutflowSignal:
    """Exchange outflow detection as accumulation indicator."""

    def test_exchange_outflow_no_penalty(self):
        """Negative exchange_net_usd (outflow from exchanges) = accumulation = no penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            exchange_outflow_usd=-35000,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "exchange_inflow" not in result.red_flags

    def test_exchange_inflow_triggers_red_flag(self):
        """Positive exchange_net_usd (inflow to exchanges) = distribution = penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            exchange_outflow_usd=75000,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "exchange_inflow" in result.red_flags
        assert result.red_flags["exchange_inflow"] == -10


class TestFreshWalletRedFlag:
    """Fresh wallet concentration triggers red flag."""

    def test_low_fresh_wallet_no_penalty(self):
        """Fresh wallet inflow below threshold = no penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            fresh_wallet_inflow_usd=8000,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "fresh_wallet_concentration" not in result.red_flags

    def test_high_fresh_wallet_triggers_penalty(self):
        """Fresh wallet inflow > $50k = concentrated fresh wallet penalty."""
        scorer = ConvictionScorer()
        signals = SignalInput(
            smart_money_whales=3,
            rug_warden_status="PASS",
            fresh_wallet_inflow_usd=85000,
        )
        result = scorer.score(signals, pot_balance_sol=14.0)
        assert "fresh_wallet_concentration" in result.red_flags
        assert result.red_flags["fresh_wallet_concentration"] == -10


class TestDCASignal:
    """Active DCA detection and counting."""

    @pytest.mark.asyncio
    async def test_dca_count_from_orders(self):
        """DCA count equals number of active orders."""
        mock = AsyncMock()
        mock.get_jupiter_dcas = AsyncMock(return_value=JUPITER_DCAS_RESPONSE)
        count = await _fetch_dca_count(mock, "ALPHA111")
        assert count == 3

    @pytest.mark.asyncio
    async def test_dca_count_empty(self):
        """No active DCAs returns 0."""
        mock = AsyncMock()
        mock.get_jupiter_dcas = AsyncMock(return_value=JUPITER_DCAS_EMPTY)
        count = await _fetch_dca_count(mock, "ALPHA111")
        assert count == 0


class TestEnrichedOutputFormat:
    """Enriched output has all new TGM fields."""

    @pytest.mark.asyncio
    async def test_enriched_signal_has_all_fields(self):
        """Each enriched signal has flow_intel, buyer_depth, dca_count, discovery_source."""
        mock = _make_nansen_mock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value={}):
                    result = await query_oracle()

        assert result["status"] == "OK"
        for sig in result["nansen_signals"]:
            assert "flow_intel" in sig, f"Missing flow_intel in {sig.get('token_mint')}"
            assert "buyer_depth" in sig, f"Missing buyer_depth in {sig.get('token_mint')}"
            assert "dca_count" in sig, f"Missing dca_count in {sig.get('token_mint')}"
            assert "discovery_source" in sig, f"Missing discovery_source in {sig.get('token_mint')}"

            fi = sig["flow_intel"]
            assert "smart_trader_net_usd" in fi
            assert "whale_net_usd" in fi
            assert "exchange_net_usd" in fi
            assert "fresh_wallet_net_usd" in fi
            assert "top_pnl_net_usd" in fi

            bd = sig["buyer_depth"]
            assert "smart_money_buyers" in bd
            assert "total_buy_volume_usd" in bd
            assert "smart_money_sellers" in bd
            assert "total_sell_volume_usd" in bd

    @pytest.mark.asyncio
    async def test_holdings_delta_in_output(self):
        """Holdings delta appears in output with positive-change tokens only."""
        mock = _make_nansen_mock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value={}):
                    result = await query_oracle()

        deltas = result.get("holdings_delta", [])
        assert len(deltas) == 3  # ALPHA, DELTA, BETA (positive only; EPSILON is negative)
        assert deltas[0]["token_symbol"] == "ALPHA"
        assert deltas[0]["balance_change_24h"] == 250000
        # Negative change should be excluded
        eps = next((d for d in deltas if d["token_symbol"] == "EPS"), None)
        assert eps is None

    @pytest.mark.asyncio
    async def test_error_returns_empty_enriched_structure(self):
        """API errors return empty nansen_signals and mobula_signals; holdings may still succeed (parallel)."""
        mock = _make_nansen_mock(
            screen_tokens=AsyncMock(side_effect=Exception("down")),
            get_smart_money_transactions=AsyncMock(side_effect=Exception("also down")),
        )

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value={}):
                    result = await query_oracle()

        assert result["status"] == "OK"
        assert result["nansen_signals"] == []
        # Holdings run in parallel and may succeed even if discovery fails
        assert isinstance(result["holdings_delta"], list)


class TestDexTradesFallback:
    """Legacy dex-trades parsing (fallback path)."""

    def test_dex_trades_filters_sells(self):
        """Sell signals (SOL as token_bought) are filtered out."""
        candidates = _parse_dex_trades_candidates(SMART_MONEY_TRANSACTIONS)
        dump = next((c for c in candidates if c["token_mint"] == "DUMP333"), None)
        assert dump is None

    def test_dex_trades_requires_3_wallets(self):
        """Tokens with <3 wallets are excluded."""
        candidates = _parse_dex_trades_candidates(SMART_MONEY_TRANSACTIONS)
        weak = next((c for c in candidates if c["token_mint"] == "WEAK222"), None)
        assert weak is None
        boar = next((c for c in candidates if c["token_mint"] == "BOAR111"), None)
        assert boar is not None
        assert boar["wallet_count"] == 4

    def test_holdings_delta_excludes_negative(self):
        """Negative balance changes are excluded from holdings delta."""
        deltas = _parse_holdings_delta(SMART_MONEY_HOLDINGS_RESPONSE)
        assert all(d["balance_change_24h"] > 0 for d in deltas)
        assert len(deltas) == 3
