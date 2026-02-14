"""Tests for VSM S5 Arbitration in heartbeat runner.

Covers:
- Grok TRADE + divergence flag → downgrade to WATCHLIST
- Grok TRADE + clean signals → stays AUTO_EXECUTE
- Telegram alert sent on S5 conflict
- Grok TRADE + low permission score → downgrade to WATCHLIST
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.scoring import ConvictionScore


# Simulate the S5 arbitration logic extracted from heartbeat_runner
# (testing the decision logic, not the full heartbeat cycle)
async def _run_s5_arbitration(score, grok_override, token_symbol, mint, result):
    """Extracted S5 arbitration logic for unit testing."""
    from lib.heartbeat_runner import _send_s5_alert

    if (score.recommendation == "AUTO_EXECUTE"
            and grok_override
            and ("verdict: TRADE" in grok_override or "verdict:TRADE" in grok_override)):
        s5_conflict = None

        if 'divergence_damping' in score.red_flags:
            s5_conflict = (
                f"S2 damping fired (no narrative) but Grok says TRADE "
                f"for {token_symbol}"
            )
        elif score.permission_score < 50:
            s5_conflict = (
                f"Grok says TRADE but permission score only "
                f"{score.permission_score} for {token_symbol}"
            )

        if s5_conflict:
            score.recommendation = "WATCHLIST"
            score.reasoning += f" | S5 ARBITRATION: {s5_conflict}"
            result["decisions"].append(f"⚖️ S5 CONFLICT: {s5_conflict}")
            await _send_s5_alert(token_symbol, mint, s5_conflict, score)


class TestS5Arbitration:
    """S5 Arbitration: Grok TRADE vs guards/flags conflict resolution."""

    @pytest.mark.asyncio
    async def test_s5_downgrades_grok_trade_with_damping(self):
        """Grok TRADE + divergence damping flag → downgrade to WATCHLIST."""
        score = ConvictionScore(
            ordering_score=50,
            permission_score=25,
            breakdown={"smart_money_oracle": 30, "rug_warden": 20},
            red_flags={"divergence_damping": -25},
            primary_sources=["oracle", "warden"],
            recommendation="AUTO_EXECUTE",
            position_size_sol=0.07,
            reasoning="Oracle: 3 whales | Warden: PASS | GROK OVERRIDE: verdict: TRADE",
        )
        result = {"decisions": []}
        grok_override = "verdict: TRADE\nreasoning: whale convergence\nconfidence: 0.8"

        with patch("lib.heartbeat_runner._send_s5_alert", new_callable=AsyncMock):
            await _run_s5_arbitration(score, grok_override, "TESTTK", "mint123456789", result)

        assert score.recommendation == "WATCHLIST"
        assert "S5 ARBITRATION" in score.reasoning
        assert any("S5 CONFLICT" in d for d in result["decisions"])

    @pytest.mark.asyncio
    async def test_s5_no_arbitration_clean_trade(self):
        """Grok TRADE + no flags + permission ≥50 → stays AUTO_EXECUTE."""
        score = ConvictionScore(
            ordering_score=85,
            permission_score=85,
            breakdown={"smart_money_oracle": 40, "narrative_hunter": 25, "rug_warden": 20},
            red_flags={},
            primary_sources=["oracle", "narrative", "warden"],
            recommendation="AUTO_EXECUTE",
            position_size_sol=0.12,
            reasoning="Oracle: 3 whales | Narrative: 8x | GROK OVERRIDE: verdict: TRADE",
        )
        result = {"decisions": []}
        grok_override = "verdict: TRADE\nreasoning: strong convergence\nconfidence: 0.9"

        with patch("lib.heartbeat_runner._send_s5_alert", new_callable=AsyncMock) as mock_alert:
            await _run_s5_arbitration(score, grok_override, "GOODTK", "mintABCDEF123456", result)

        assert score.recommendation == "AUTO_EXECUTE"
        assert "S5 ARBITRATION" not in score.reasoning
        mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_s5_alert_sent_on_conflict(self):
        """Verify _send_s5_alert called with Telegram API on conflict."""
        score = ConvictionScore(
            ordering_score=50,
            permission_score=25,
            breakdown={"smart_money_oracle": 30, "rug_warden": 20},
            red_flags={"divergence_damping": -25},
            primary_sources=["oracle", "warden"],
            recommendation="AUTO_EXECUTE",
            position_size_sol=0.07,
            reasoning="GROK OVERRIDE: verdict: TRADE",
        )
        result = {"decisions": []}
        grok_override = "verdict: TRADE\nreasoning: whale convergence\nconfidence: 0.8"
        mint = "mint123456789ABC"

        mock_post = AsyncMock()
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("lib.heartbeat_runner.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "testtoken", "TELEGRAM_CHANNEL_ID": "testchan"}):
            from lib.heartbeat_runner import _send_s5_alert
            await _run_s5_arbitration(score, grok_override, "TESTTK", mint, result)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "api.telegram.org" in call_args[0][0]
        assert "S5 ARBITRATION" in call_args[1]["json"]["text"]

    @pytest.mark.asyncio
    async def test_s5_low_permission_downgrade(self):
        """Grok TRADE + permission < 50 (no damping flag) → WATCHLIST."""
        score = ConvictionScore(
            ordering_score=45,
            permission_score=45,
            breakdown={"smart_money_oracle": 30, "rug_warden": 15},
            red_flags={"concentrated_volume": -15},
            primary_sources=["oracle", "warden"],
            recommendation="AUTO_EXECUTE",
            position_size_sol=0.06,
            reasoning="GROK OVERRIDE: verdict: TRADE",
        )
        result = {"decisions": []}
        grok_override = "verdict: TRADE\nreasoning: pattern match\nconfidence: 0.7"

        with patch("lib.heartbeat_runner._send_s5_alert", new_callable=AsyncMock):
            await _run_s5_arbitration(score, grok_override, "LOWTK", "mintLOW123456789", result)

        assert score.recommendation == "WATCHLIST"
        assert "permission score only 45" in score.reasoning
