---
name: self-repair
description: Gateway diagnostics via Grok, whitelist commands, human-gate restarts.
---

# Self-Repair — Gateway Diagnostics

## Purpose

Automated gateway diagnosis via Grok. When the OpenClaw gateway session collapses
(NO_REPLY loops, zombie PIDs, crashes), this skill gathers logs, feeds them to Grok
for root cause analysis, and alerts G with the exact fix command.

**Phase 1: Diagnose + alert only.** Never executes restarts or destructive commands.
G copy-pastes the suggested command.

## When to Use

- Gateway stops responding to Telegram messages
- Heartbeat cycles produce NO_REPLY or empty output
- Zombie gateway PIDs detected
- On-demand when G asks "what's wrong with the gateway?"

## Command Whitelist

### Read-Only (auto-executed during diagnosis)

```
journalctl --user -u openclaw-gateway.service -n {20-100}
systemctl --user status openclaw-gateway.service
git status                    (cwd: /home/autistboar/chadboar)
git log --oneline -{1-10}     (cwd: /home/autistboar/chadboar)
```

### Human-Gated (suggested to G, never auto-executed)

```
systemctl --user restart openclaw-gateway.service
rm ~/.openclaw/agents/<agent>/sessions/<file>.jsonl
```

### Blocked (everything else)

- No `cat` (prevents .env leakage)
- No `pip`, `curl`, `wget`, `sudo`, `rm` (except session files), `mv`, `chmod`
- No `git push/commit/add`
- Whitelist is a Python set of regex patterns, checked before any subprocess call

## Risk Model

- **Diagnose-only**: Skill reads logs and status. Never executes restarts.
- **INV-KILLSWITCH**: Checked first — if active, abort immediately with no diagnosis.
- **INV-BLIND-KEY**: No key access — skill only reads logs and gateway status.
- **Self-restart prohibition**: Respects AGENTS.md — Boar must NEVER restart his own gateway.

## Output Format

```json
{
    "status": "OK",
    "diagnosis": {
        "root_cause": "session_collapse",
        "severity": "critical",
        "reasoning": "5 consecutive NO_REPLY outputs...",
        "suggested_cmd": "rm ~/.openclaw/agents/main/sessions/abc123.jsonl",
        "cmd_approved": false,
        "gateway_status": "active (running)"
    },
    "alert_sent": true,
    "bead_id": "20260214_081500.yaml"
}
```

## Bead Logging

Repair beads are written to `beads/self-repair/` as timestamped YAML files.
These are simple diagnostic records — no vector embeddings needed.

## Commands

```bash
# Full diagnosis (gather logs + Grok analysis + Telegram alert)
python3 -m lib.skills.self_repair

# Status-only (systemctl status, no Grok call)
python3 -m lib.skills.self_repair --status-only
```
