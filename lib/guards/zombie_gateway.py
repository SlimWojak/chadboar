"""Zombie gateway detection guard.

Checks that only one openclaw-gateway process is running. Multiple PIDs
indicate a stale gateway from a prior era — causes Telegram auth conflicts,
API failures, and session corruption.

Proven failure mode: Feb 12 2026 outage — stale gateway held Telegram
bot token, prevented new gateway from authenticating.

Usage:
    python3 -m lib.guards.zombie_gateway

Exit codes:
    0 = exactly 0 or 1 gateway process (healthy)
    1 = multiple gateway processes detected (zombie alert)

Output:
    JSON with status, pid list, and message.
"""

from __future__ import annotations

import json
import sys


def check_zombie_gateway() -> dict:
    """Check for multiple openclaw-gateway processes.

    Uses /proc filesystem directly to avoid pgrep self-matching
    (pgrep -f matches its own command line containing the search string).
    """
    import os

    pids: list[int] = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                comm_path = f"/proc/{entry}/comm"
                with open(comm_path) as f:
                    comm = f.read().strip()
                # /proc/PID/comm truncates to 15 chars: "openclaw-gatewa"
                if comm == "openclaw-gatewa":
                    pids.append(int(entry))
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
    except OSError:
        return {
            "status": "WARN",
            "message": "Could not check gateway processes.",
            "pids": [],
        }

    if len(pids) <= 1:
        return {
            "status": "CLEAR",
            "message": f"Single gateway process running (PID {pids[0]})." if pids else "No gateway process found.",
            "pids": pids,
        }

    return {
        "status": "ZOMBIE",
        "message": (
            f"ZOMBIE ALERT: {len(pids)} gateway processes detected. "
            f"PIDs: {pids}. Stale process will cause auth conflicts. "
            f"Alert G with 🔴 CRITICAL — only G can kill the zombie."
        ),
        "pids": pids,
        "alert": True,
    }


def main() -> None:
    result = check_zombie_gateway()
    print(json.dumps(result, indent=2))
    sys.exit(1 if result["status"] == "ZOMBIE" else 0)


if __name__ == "__main__":
    main()
