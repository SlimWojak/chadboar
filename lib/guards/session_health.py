"""Session health guard — detect context collapse.

Cheap models (DeepSeek) latch onto shortest patterns in accumulating sessions.
When heartbeat outputs shrink to ~5 tokens ("HEARTBEAT_OK"), the session has
collapsed. This guard checks the last N heartbeat assistant outputs and warns
if 3+ consecutive responses are under a token threshold.

Proven failure mode: Feb 12 2026 — DeepSeek responded "HEARTBEAT_OK" (5 tokens)
for 8+ hours. Gateway marked them silent, no Telegram delivery.

Usage:
    python3 -m lib.guards.session_health

Exit codes:
    0 = session healthy (recent outputs are substantive)
    1 = session may be collapsing (consecutive short outputs)

Output:
    JSON with status, recent output lengths, and message.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SESSIONS_DIR = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
SESSIONS_INDEX = SESSIONS_DIR / "sessions.json"

# Thresholds
MIN_OUTPUT_TOKENS = 20  # Outputs below this are suspiciously short
CONSECUTIVE_THRESHOLD = 3  # N consecutive short outputs = warning


def find_main_session_file() -> Path | None:
    """Find the session file for agent:main:main."""
    if not SESSIONS_INDEX.exists():
        return None

    index = json.loads(SESSIONS_INDEX.read_text())

    # sessions.json can be a dict keyed by session name, or have a "sessions" list
    if isinstance(index, dict):
        # Try direct key lookup first (dict format: {key: {sessionId, ...}})
        main_entry = index.get("agent:main:main")
        if isinstance(main_entry, dict):
            session_id = main_entry.get("sessionId", "")
            if session_id:
                candidate = SESSIONS_DIR / f"{session_id}.jsonl"
                if candidate.exists():
                    return candidate

        # Fall back to scanning a "sessions" list if present
        for entry in index.get("sessions", []):
            if isinstance(entry, dict) and entry.get("key") == "agent:main:main":
                session_id = entry.get("sessionId", "")
                if session_id:
                    candidate = SESSIONS_DIR / f"{session_id}.jsonl"
                    if candidate.exists():
                        return candidate

    return None


def get_recent_assistant_outputs(session_file: Path, n: int = 5) -> list[dict]:
    """Extract the last N assistant text outputs from a session JSONL file."""
    outputs = []
    with open(session_file) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "message":
                continue

            msg = entry.get("message", {})
            if msg.get("role") != "assistant":
                continue

            # Only count text outputs (not tool calls)
            for content in msg.get("content", []):
                if content.get("type") == "text":
                    text = content.get("text", "")
                    usage = msg.get("usage", {})
                    output_tokens = usage.get("output", len(text.split()))
                    outputs.append({
                        "text": text[:100],
                        "output_tokens": output_tokens,
                        "model": msg.get("model", "unknown"),
                    })
                    break  # One text output per assistant message

    return outputs[-n:] if outputs else []


def check_session_health() -> dict:
    """Check if the heartbeat session shows signs of context collapse."""
    session_file = find_main_session_file()

    if session_file is None:
        return {
            "status": "CLEAR",
            "message": "No main session file found. Fresh session — healthy.",
            "recent_outputs": [],
        }

    recent = get_recent_assistant_outputs(session_file, n=5)

    if len(recent) < CONSECUTIVE_THRESHOLD:
        return {
            "status": "CLEAR",
            "message": f"Only {len(recent)} outputs in session. Too early to assess.",
            "recent_outputs": recent,
        }

    # Check for consecutive short outputs (only heartbeat model, not Sonnet)
    consecutive_short = 0
    for output in reversed(recent):
        if output["output_tokens"] < MIN_OUTPUT_TOKENS:
            consecutive_short += 1
        else:
            break

    if consecutive_short >= CONSECUTIVE_THRESHOLD:
        return {
            "status": "COLLAPSING",
            "message": (
                f"SESSION COLLAPSE WARNING: {consecutive_short} consecutive "
                f"heartbeat outputs under {MIN_OUTPUT_TOKENS} tokens. "
                f"DeepSeek may be pattern-locked. Consider session reset. "
                f"Fix: delete session file, restart gateway."
            ),
            "consecutive_short": consecutive_short,
            "recent_outputs": recent,
            "session_file": str(session_file),
            "alert": True,
        }

    return {
        "status": "CLEAR",
        "message": f"Session healthy. Last {len(recent)} outputs look substantive.",
        "consecutive_short": consecutive_short,
        "recent_outputs": recent,
    }


def main() -> None:
    result = check_session_health()
    print(json.dumps(result, indent=2))
    sys.exit(1 if result["status"] == "COLLAPSING" else 0)


if __name__ == "__main__":
    main()
