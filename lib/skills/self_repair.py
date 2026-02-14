"""Self-Repair skill — gateway diagnostics via Grok.

Phase 1: Diagnose + alert only. Never executes restarts.
Feeds gateway logs to Grok for root cause analysis, validates suggested
fix commands against a strict whitelist, and alerts G via Telegram.

Respects AGENTS.md: "Boar must NEVER restart his own gateway."

Usage:
    python3 -m lib.skills.self_repair              # full diagnosis
    python3 -m lib.skills.self_repair --status-only # systemctl status only, no Grok

Output:
    JSON with diagnosis, alert status, and bead ID.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parent.parent.parent
BEADS_DIR = WORKSPACE / "beads" / "self-repair"

# ── Whitelist ────────────────────────────────────────────────────────
# Hardcoded. Not configurable. Checked before any subprocess call.

# Read-only commands: auto-executed during diagnostics
_READ_ONLY_PATTERNS: list[re.Pattern] = [
    re.compile(r"^journalctl\s+--user\s+-u\s+openclaw-gateway\.service\s+-n\s+\d{1,3}$"),
    re.compile(r"^systemctl\s+--user\s+status\s+openclaw-gateway\.service$"),
    re.compile(r"^git\s+status$"),
    re.compile(r"^git\s+log\s+--oneline\s+-\d{1,2}$"),
]

# Human-gated commands: suggested to G, never auto-executed
_HUMAN_GATED_PATTERNS: list[re.Pattern] = [
    re.compile(r"^systemctl\s+--user\s+restart\s+openclaw-gateway\.service$"),
    re.compile(
        r"^rm\s+~/.openclaw/agents/[a-zA-Z0-9_-]+/sessions/[a-zA-Z0-9_.-]+\.jsonl$"
    ),
]


def _validate_command(cmd: str) -> tuple[bool, str]:
    """Check if a command is on the whitelist.

    Returns:
        (allowed, reason) — allowed=True if on whitelist, reason explains why.
    """
    cmd = cmd.strip()

    for pattern in _READ_ONLY_PATTERNS:
        if pattern.match(cmd):
            return True, "read-only"

    for pattern in _HUMAN_GATED_PATTERNS:
        if pattern.match(cmd):
            return True, "human-gated"

    return False, f"BLOCKED — not on whitelist: {cmd}"


def _is_human_gated(cmd: str) -> bool:
    """Return True if the command requires human approval."""
    cmd = cmd.strip()
    return any(p.match(cmd) for p in _HUMAN_GATED_PATTERNS)


# ── Killswitch ───────────────────────────────────────────────────────


def _check_killswitch() -> bool:
    """Return True if killswitch is active (halt immediately)."""
    return (WORKSPACE / "killswitch.txt").exists()


# ── Diagnostics Gathering ────────────────────────────────────────────


async def _gather_diagnostics() -> str:
    """Run whitelisted read-only commands and return concatenated output."""
    commands = [
        ("journalctl --user -u openclaw-gateway.service -n 50", None),
        ("systemctl --user status openclaw-gateway.service", None),
    ]

    sections: list[str] = []
    for cmd, cwd in commands:
        allowed, reason = _validate_command(cmd)
        if not allowed:
            sections.append(f"=== {cmd} ===\nBLOCKED: {reason}\n")
            continue

        try:
            result = subprocess.run(
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=10,
                cwd=cwd,
            )
            output = result.stdout or result.stderr or "(no output)"
            sections.append(f"=== {cmd} ===\n{output}\n")
        except subprocess.TimeoutExpired:
            sections.append(f"=== {cmd} ===\nTIMEOUT after 10s\n")
        except Exception as e:
            sections.append(f"=== {cmd} ===\nERROR: {e}\n")

    return "\n".join(sections)


async def _get_gateway_status() -> str:
    """Get just the systemctl status output."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "status", "openclaw-gateway.service"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout or result.stderr or "(no output)"
    except Exception as e:
        return f"ERROR: {e}"


# ── Grok Analysis ───────────────────────────────────────────────────

GROK_SYSTEM_PROMPT = """\
You are a gateway diagnostician for ChadBoar (OpenClaw gateway on Linux).
Analyze the journalctl and systemctl output provided.

Rules:
- Identify the root cause: session_collapse, gateway_crash, timeout, zombie_pid, healthy, or other
- Suggest exactly ONE fix command from this whitelist ONLY:
  * systemctl --user restart openclaw-gateway.service
  * rm ~/.openclaw/agents/<agent>/sessions/<file>.jsonl  (use actual paths from logs)
- If the gateway is healthy, suggest no command (set suggested_cmd to null)
- Never suggest commands outside the whitelist

Output ONLY valid YAML (no markdown fences, no extra text):
diagnosis: <one-line summary>
root_cause: <session_collapse|gateway_crash|timeout|zombie_pid|healthy|unknown>
severity: <critical|warning|info>
reasoning: <2-3 sentence analysis>
suggested_cmd: <whitelisted command or null>
"""


async def _call_grok(diagnostics: str) -> dict[str, Any]:
    """Send diagnostics to Grok for analysis. Returns parsed YAML dict."""
    from lib.llm_utils import call_grok

    result = await call_grok(
        prompt=f"Analyze this gateway diagnostic output:\n\n{diagnostics}",
        system_prompt=GROK_SYSTEM_PROMPT,
        max_tokens=512,
        temperature=0.2,
    )

    if result.get("status") != "OK":
        return {
            "diagnosis": "Grok API call failed",
            "root_cause": "unknown",
            "severity": "warning",
            "reasoning": result.get("error", "Unknown error"),
            "suggested_cmd": None,
        }

    content = result.get("content", "")
    try:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise ValueError("Grok response is not a YAML dict")
        return parsed
    except Exception:
        return {
            "diagnosis": "Failed to parse Grok response",
            "root_cause": "unknown",
            "severity": "warning",
            "reasoning": f"Raw response: {content[:300]}",
            "suggested_cmd": None,
        }


# ── Telegram Alert ───────────────────────────────────────────────────


async def _send_telegram_alert(diagnosis: dict) -> bool:
    """Send WARNING alert to G with diagnosis + suggested command."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "")

    if not token or not channel_id:
        print("WARN: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set", file=sys.stderr)
        return False

    severity = diagnosis.get("severity", "warning")
    emoji = {"critical": "\U0001f534", "warning": "\U0001f7e1", "info": "\U0001f7e2"}.get(
        severity, "\U0001f7e1"
    )

    suggested = diagnosis.get("suggested_cmd")
    cmd_line = ""
    if suggested:
        approved = not _is_human_gated(suggested)
        gate = "(auto)" if approved else "(HUMAN-GATE: copy-paste to execute)"
        cmd_line = f"\n\nFix: `{suggested}` {gate}"

    text = (
        f"{emoji} SELF-REPAIR DIAGNOSIS\n\n"
        f"Root cause: {diagnosis.get('root_cause', 'unknown')}\n"
        f"Severity: {severity}\n"
        f"Analysis: {diagnosis.get('reasoning', 'N/A')}"
        f"{cmd_line}"
    )

    try:
        from telegram import Bot

        bot = Bot(token=token)
        await bot.send_message(chat_id=channel_id, text=text)
        return True
    except Exception as e:
        print(f"WARN: Telegram send failed: {e}", file=sys.stderr)
        return False


# ── Bead Logging ─────────────────────────────────────────────────────


def _log_repair_bead(diagnosis: dict) -> str:
    """Write YAML bead to beads/self-repair/YYYYMMDD_HHMMSS.yaml.

    Returns the bead filename.
    """
    BEADS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    filename = now.strftime("%Y%m%d_%H%M%S") + ".yaml"
    filepath = BEADS_DIR / filename

    bead = {
        "timestamp": now.isoformat(),
        "root_cause": diagnosis.get("root_cause", "unknown"),
        "severity": diagnosis.get("severity", "unknown"),
        "reasoning": diagnosis.get("reasoning", ""),
        "suggested_cmd": diagnosis.get("suggested_cmd"),
        "cmd_executed": False,
        "gateway_status": diagnosis.get("gateway_status", ""),
        "grok_model": "grok-4-1-fast-reasoning",
    }

    filepath.write_text(yaml.dump(bead, default_flow_style=False, sort_keys=False))
    return filename


# ── Main Entry Point ─────────────────────────────────────────────────


async def diagnose_gateway(status_only: bool = False) -> dict[str, Any]:
    """Main entry point. Gather diagnostics, ask Grok, return diagnosis.

    Args:
        status_only: If True, just return systemctl status without Grok analysis.

    Returns:
        Structured diagnosis dict with alert and bead info.
    """
    # INV-KILLSWITCH: check first, abort immediately if active
    if _check_killswitch():
        return {
            "status": "KILLSWITCH",
            "diagnosis": {
                "root_cause": "killswitch_active",
                "severity": "info",
                "reasoning": "Killswitch is active. No diagnosis performed.",
                "suggested_cmd": None,
                "cmd_approved": False,
                "gateway_status": "",
            },
            "alert_sent": False,
            "bead_id": "",
        }

    # Status-only mode: just systemctl, no Grok
    if status_only:
        gateway_status = await _get_gateway_status()
        return {
            "status": "OK",
            "diagnosis": {
                "root_cause": "status_check",
                "severity": "info",
                "reasoning": "Status-only check requested.",
                "suggested_cmd": None,
                "cmd_approved": False,
                "gateway_status": gateway_status,
            },
            "alert_sent": False,
            "bead_id": "",
        }

    # Full diagnosis
    diagnostics = await _gather_diagnostics()
    gateway_status = await _get_gateway_status()

    # Ask Grok for analysis
    grok_result = await _call_grok(diagnostics)
    grok_result["gateway_status"] = gateway_status

    # Validate suggested command
    suggested = grok_result.get("suggested_cmd")
    cmd_approved = False
    if suggested:
        allowed, reason = _validate_command(suggested)
        if not allowed:
            grok_result["suggested_cmd"] = None
            grok_result["reasoning"] = (
                grok_result.get("reasoning", "")
                + f" [Suggested cmd blocked: {reason}]"
            )
        else:
            cmd_approved = not _is_human_gated(suggested)

    grok_result["cmd_approved"] = cmd_approved

    # Send Telegram alert (skip for healthy gateway)
    alert_sent = False
    if grok_result.get("root_cause") != "healthy":
        alert_sent = await _send_telegram_alert(grok_result)

    # Log bead
    bead_id = _log_repair_bead(grok_result)

    return {
        "status": "OK",
        "diagnosis": grok_result,
        "alert_sent": alert_sent,
        "bead_id": bead_id,
    }


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    status_only = "--status-only" in sys.argv
    result = asyncio.run(diagnose_gateway(status_only=status_only))
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["status"] in ("OK", "KILLSWITCH") else 1)


if __name__ == "__main__":
    main()
