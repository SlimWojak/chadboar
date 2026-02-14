"""Tests for Self-Repair skill.

Covers:
- Command whitelist validation (read-only, human-gated, blocked)
- Killswitch abort behavior
- Grok response parsing
- Healthy gateway → no alert
- Human-gated command marking
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.skills.self_repair import (
    WORKSPACE,
    _is_human_gated,
    _log_repair_bead,
    _validate_command,
    diagnose_gateway,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_workspace(tmp_path, monkeypatch):
    """Redirect workspace paths to tmp for test isolation."""
    monkeypatch.setattr("lib.skills.self_repair.WORKSPACE", tmp_path)
    monkeypatch.setattr("lib.skills.self_repair.BEADS_DIR", tmp_path / "beads" / "self-repair")
    yield tmp_path


# ── Whitelist Tests ───────────────────────────────────────────────────


class TestWhitelist:
    """Command whitelist validation."""

    def test_whitelist_allows_journalctl(self):
        allowed, reason = _validate_command(
            "journalctl --user -u openclaw-gateway.service -n 50"
        )
        assert allowed is True
        assert reason == "read-only"

    def test_whitelist_allows_journalctl_various_counts(self):
        for n in [20, 50, 100]:
            allowed, _ = _validate_command(
                f"journalctl --user -u openclaw-gateway.service -n {n}"
            )
            assert allowed is True

    def test_whitelist_allows_systemctl_status(self):
        allowed, reason = _validate_command(
            "systemctl --user status openclaw-gateway.service"
        )
        assert allowed is True
        assert reason == "read-only"

    def test_whitelist_allows_git_status(self):
        allowed, reason = _validate_command("git status")
        assert allowed is True
        assert reason == "read-only"

    def test_whitelist_allows_git_log(self):
        allowed, reason = _validate_command("git log --oneline -5")
        assert allowed is True
        assert reason == "read-only"

    def test_whitelist_blocks_cat_env(self):
        allowed, reason = _validate_command("cat .env")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_blocks_sudo(self):
        allowed, reason = _validate_command("sudo systemctl restart something")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_blocks_rm_arbitrary(self):
        allowed, reason = _validate_command("rm /etc/passwd")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_blocks_curl(self):
        allowed, reason = _validate_command("curl https://evil.com")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_blocks_pip(self):
        allowed, reason = _validate_command("pip install malware")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_blocks_git_push(self):
        allowed, reason = _validate_command("git push origin main")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_blocks_git_commit(self):
        allowed, reason = _validate_command("git commit -m 'oops'")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_allows_rm_session(self):
        allowed, reason = _validate_command(
            "rm ~/.openclaw/agents/main/sessions/abc123.jsonl"
        )
        assert allowed is True
        assert reason == "human-gated"

    def test_whitelist_allows_restart(self):
        allowed, reason = _validate_command(
            "systemctl --user restart openclaw-gateway.service"
        )
        assert allowed is True
        assert reason == "human-gated"

    def test_whitelist_blocks_rm_non_session(self):
        allowed, reason = _validate_command("rm ~/.openclaw/config.json")
        assert allowed is False
        assert "BLOCKED" in reason

    def test_whitelist_blocks_journalctl_too_many_lines(self):
        # 4+ digit line count not allowed by regex
        allowed, _ = _validate_command(
            "journalctl --user -u openclaw-gateway.service -n 1000"
        )
        assert allowed is False


class TestHumanGated:
    """Human-gated command detection."""

    def test_restart_marked_human_gated(self):
        assert _is_human_gated(
            "systemctl --user restart openclaw-gateway.service"
        ) is True

    def test_rm_session_marked_human_gated(self):
        assert _is_human_gated(
            "rm ~/.openclaw/agents/main/sessions/abc123.jsonl"
        ) is True

    def test_journalctl_not_human_gated(self):
        assert _is_human_gated(
            "journalctl --user -u openclaw-gateway.service -n 50"
        ) is False

    def test_systemctl_status_not_human_gated(self):
        assert _is_human_gated(
            "systemctl --user status openclaw-gateway.service"
        ) is False


# ── Killswitch Tests ──────────────────────────────────────────────────


class TestKillswitch:
    """INV-KILLSWITCH: killswitch active → no diagnosis."""

    async def test_killswitch_aborts(self, isolate_workspace):
        ks_file = isolate_workspace / "killswitch.txt"
        ks_file.write_text("Emergency halt")

        result = await diagnose_gateway()
        assert result["status"] == "KILLSWITCH"
        assert result["diagnosis"]["root_cause"] == "killswitch_active"
        assert result["alert_sent"] is False
        assert result["bead_id"] == ""


# ── Grok Response Parsing ────────────────────────────────────────────


class TestGrokParsing:
    """Grok YAML response parsing."""

    async def test_grok_response_parsed(self, isolate_workspace):
        mock_grok_yaml = (
            "diagnosis: Session context exhausted\n"
            "root_cause: session_collapse\n"
            "severity: critical\n"
            "reasoning: 5 consecutive NO_REPLY outputs in logs\n"
            "suggested_cmd: rm ~/.openclaw/agents/main/sessions/abc123.jsonl\n"
        )

        mock_grok_result = {
            "status": "OK",
            "content": mock_grok_yaml,
            "model": "grok-4-1-fast-reasoning",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        with (
            patch("lib.skills.self_repair._gather_diagnostics", new_callable=AsyncMock) as mock_diag,
            patch("lib.skills.self_repair._get_gateway_status", new_callable=AsyncMock) as mock_status,
            patch("lib.skills.self_repair._send_telegram_alert", new_callable=AsyncMock) as mock_tg,
            patch("lib.llm_utils.call_grok", new_callable=AsyncMock) as mock_grok,
        ):
            mock_diag.return_value = "=== journalctl ===\nNO_REPLY\nNO_REPLY\n"
            mock_status.return_value = "active (running)"
            mock_tg.return_value = True
            mock_grok.return_value = mock_grok_result

            result = await diagnose_gateway()

        assert result["status"] == "OK"
        assert result["diagnosis"]["root_cause"] == "session_collapse"
        assert result["diagnosis"]["severity"] == "critical"
        assert result["diagnosis"]["cmd_approved"] is False  # rm is human-gated
        assert result["diagnosis"]["suggested_cmd"] == "rm ~/.openclaw/agents/main/sessions/abc123.jsonl"
        assert result["alert_sent"] is True
        assert result["bead_id"].endswith(".yaml")

    async def test_grok_blocked_command_stripped(self, isolate_workspace):
        """If Grok suggests a non-whitelisted command, it gets stripped."""
        mock_grok_yaml = (
            "diagnosis: Gateway crashed\n"
            "root_cause: gateway_crash\n"
            "severity: critical\n"
            "reasoning: Segfault in logs\n"
            "suggested_cmd: sudo reboot\n"
        )

        mock_grok_result = {
            "status": "OK",
            "content": mock_grok_yaml,
            "model": "grok-4-1-fast-reasoning",
            "usage": {},
        }

        with (
            patch("lib.skills.self_repair._gather_diagnostics", new_callable=AsyncMock) as mock_diag,
            patch("lib.skills.self_repair._get_gateway_status", new_callable=AsyncMock) as mock_status,
            patch("lib.skills.self_repair._send_telegram_alert", new_callable=AsyncMock) as mock_tg,
            patch("lib.llm_utils.call_grok", new_callable=AsyncMock) as mock_grok,
        ):
            mock_diag.return_value = "crash output"
            mock_status.return_value = "inactive (dead)"
            mock_tg.return_value = True
            mock_grok.return_value = mock_grok_result

            result = await diagnose_gateway()

        # Blocked command should be stripped
        assert result["diagnosis"]["suggested_cmd"] is None
        assert "BLOCKED" in result["diagnosis"]["reasoning"]


# ── Healthy Gateway ──────────────────────────────────────────────────


class TestHealthyGateway:
    """Healthy gateway should not trigger alerts."""

    async def test_healthy_gateway_no_alert(self, isolate_workspace):
        mock_grok_yaml = (
            "diagnosis: Gateway is healthy\n"
            "root_cause: healthy\n"
            "severity: info\n"
            "reasoning: All systems nominal\n"
            "suggested_cmd: null\n"
        )

        mock_grok_result = {
            "status": "OK",
            "content": mock_grok_yaml,
            "model": "grok-4-1-fast-reasoning",
            "usage": {},
        }

        with (
            patch("lib.skills.self_repair._gather_diagnostics", new_callable=AsyncMock) as mock_diag,
            patch("lib.skills.self_repair._get_gateway_status", new_callable=AsyncMock) as mock_status,
            patch("lib.skills.self_repair._send_telegram_alert", new_callable=AsyncMock) as mock_tg,
            patch("lib.llm_utils.call_grok", new_callable=AsyncMock) as mock_grok,
        ):
            mock_diag.return_value = "=== systemctl ===\nactive (running)\n"
            mock_status.return_value = "active (running)"
            mock_grok.return_value = mock_grok_result

            result = await diagnose_gateway()

        # Healthy → no Telegram alert sent
        assert result["status"] == "OK"
        assert result["diagnosis"]["root_cause"] == "healthy"
        mock_tg.assert_not_called()
        assert result["alert_sent"] is False


# ── Bead Logging ─────────────────────────────────────────────────────


class TestBeadLogging:
    """Repair bead file creation."""

    def test_bead_created(self, isolate_workspace):
        diagnosis = {
            "root_cause": "session_collapse",
            "severity": "critical",
            "reasoning": "Test diagnosis",
            "suggested_cmd": None,
            "gateway_status": "active (running)",
        }

        bead_id = _log_repair_bead(diagnosis)
        assert bead_id.endswith(".yaml")

        bead_dir = isolate_workspace / "beads" / "self-repair"
        bead_file = bead_dir / bead_id
        assert bead_file.exists()

        import yaml
        content = yaml.safe_load(bead_file.read_text())
        assert content["root_cause"] == "session_collapse"
        assert content["severity"] == "critical"
        assert content["cmd_executed"] is False
        assert content["grok_model"] == "grok-4-1-fast-reasoning"


# ── Status-Only Mode ─────────────────────────────────────────────────


class TestStatusOnly:
    """--status-only mode skips Grok."""

    async def test_status_only_mode(self, isolate_workspace):
        with patch(
            "lib.skills.self_repair._get_gateway_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = "active (running) since Mon 2026-02-14"

            result = await diagnose_gateway(status_only=True)

        assert result["status"] == "OK"
        assert result["diagnosis"]["root_cause"] == "status_check"
        assert "active (running)" in result["diagnosis"]["gateway_status"]
        assert result["alert_sent"] is False
