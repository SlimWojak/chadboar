"""Tests for Rug Warden — INV-RUG-WARDEN-VETO.

Validates that known rug tokens are rejected and clean tokens pass.
Uses mocked Birdeye API responses.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from lib.skills.warden_check import check_token
from tests.mocks.mock_birdeye import (
    CLEAN_TOKEN_OVERVIEW,
    CLEAN_TOKEN_SECURITY,
    RUG_TOKEN_OVERVIEW,
    RUG_TOKEN_SECURITY,
    WARN_TOKEN_OVERVIEW,
    WARN_TOKEN_SECURITY,
)


def _mock_birdeye(overview: dict, security: dict):
    """Create a mock BirdeyeClient that returns specified data."""
    mock = AsyncMock()
    mock.get_token_overview = AsyncMock(return_value=overview)
    mock.get_token_security = AsyncMock(return_value=security)
    mock.close = AsyncMock()
    return mock


class TestRugWarden:
    """INV-RUG-WARDEN-VETO: FAIL = no trade, no override."""

    @pytest.mark.asyncio
    async def test_clean_token_passes(self):
        """Clean token with good liquidity, low concentration → PASS."""
        mock = _mock_birdeye(CLEAN_TOKEN_OVERVIEW, CLEAN_TOKEN_SECURITY)
        with patch("lib.skills.warden_check.BirdeyeClient", return_value=mock):
            result = await check_token("CLEANmint111111111111111111111111111111111")

        assert result["verdict"] == "PASS"
        assert result["checks"]["liquidity_usd"] == 85000
        assert result["checks"]["holder_concentration_pct"] == 35.0
        assert result["checks"]["mint_authority_mutable"] is False
        assert len(result["reasons"]) == 0

    @pytest.mark.asyncio
    async def test_rug_token_fails(self):
        """Rug token with low liquidity, high concentration, mutable mint → FAIL."""
        mock = _mock_birdeye(RUG_TOKEN_OVERVIEW, RUG_TOKEN_SECURITY)
        with patch("lib.skills.warden_check.BirdeyeClient", return_value=mock):
            result = await check_token("RUGmint2222222222222222222222222222222222222")

        assert result["verdict"] == "FAIL"
        assert len(result["reasons"]) >= 2  # Multiple failure reasons
        # Check specific failure reasons exist
        reason_text = " ".join(result["reasons"])
        assert "Liquidity" in reason_text
        assert "holders" in reason_text.lower() or "Mutable" in reason_text

    @pytest.mark.asyncio
    async def test_warn_token_warns(self):
        """New token with unlocked LP → WARN (not FAIL)."""
        mock = _mock_birdeye(WARN_TOKEN_OVERVIEW, WARN_TOKEN_SECURITY)
        with patch("lib.skills.warden_check.BirdeyeClient", return_value=mock):
            result = await check_token("WARNmint333333333333333333333333333333333333")

        assert result["verdict"] == "WARN"
        assert len(result["reasons"]) >= 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_fail(self):
        """If API call fails, verdict is FAIL (safe default)."""
        mock = AsyncMock()
        mock.get_token_overview = AsyncMock(side_effect=Exception("API timeout"))
        mock.close = AsyncMock()
        with patch("lib.skills.warden_check.BirdeyeClient", return_value=mock):
            result = await check_token("anything")

        assert result["verdict"] == "FAIL"
        assert any("failed" in r.lower() for r in result["reasons"])
