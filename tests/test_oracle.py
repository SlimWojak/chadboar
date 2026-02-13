"""Tests for Smart Money Oracle.

Validates signal parsing, filtering (3+ wallets), and error handling.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from lib.skills.oracle_query import query_oracle
from tests.mocks.mock_nansen import SMART_MONEY_TRANSACTIONS, TOKEN_SMART_MONEY


class TestSmartMoneyOracle:
    """Smart Money Oracle signal detection."""

    @pytest.mark.asyncio
    async def test_broad_scan_finds_convergent_signals(self):
        """Tokens with 3+ independent wallets buying should be detected."""
        mock = AsyncMock()
        mock.get_smart_money_transactions = AsyncMock(return_value=SMART_MONEY_TRANSACTIONS)
        mock.close = AsyncMock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            result = await query_oracle()

        assert result["status"] == "OK"
        assert result["count"] >= 1

        # BOAR has 4 wallets → should be in signals
        boar_signal = next((s for s in result["signals"] if s["token_symbol"] == "BOAR"), None)
        assert boar_signal is not None
        assert boar_signal["wallet_count"] == 4
        assert boar_signal["confidence"] in ("medium", "high")

        # WEAK has only 2 wallets → should NOT be in signals
        weak_signal = next((s for s in result["signals"] if s["token_symbol"] == "WEAK"), None)
        assert weak_signal is None

    @pytest.mark.asyncio
    async def test_sell_signals_filtered_out(self):
        """Sell/dump signals should not appear as buy signals."""
        mock = AsyncMock()
        mock.get_smart_money_transactions = AsyncMock(return_value=SMART_MONEY_TRANSACTIONS)
        mock.close = AsyncMock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            result = await query_oracle()

        dump_signal = next((s for s in result["signals"] if s["token_symbol"] == "DUMP"), None)
        assert dump_signal is None

    @pytest.mark.asyncio
    async def test_token_specific_query(self):
        """Query for a specific token returns wallet details."""
        mock = AsyncMock()
        mock.get_token_smart_money = AsyncMock(return_value=TOKEN_SMART_MONEY)
        mock.close = AsyncMock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            result = await query_oracle(token_mint="BOAR111")

        assert result["status"] == "OK"
        assert result["count"] == 1
        assert result["signals"][0]["wallet_count"] == 5
        assert result["signals"][0]["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self):
        """API errors return OK status with empty signals."""
        mock = AsyncMock()
        mock.get_smart_money_transactions = AsyncMock(side_effect=Exception("timeout"))
        mock.close = AsyncMock()

        with patch("lib.skills.oracle_query.NansenClient", return_value=mock):
            result = await query_oracle()

        assert result["status"] == "ERROR"
        assert result["count"] == 0
