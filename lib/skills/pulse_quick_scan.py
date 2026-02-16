"""Pulse Quick Scanner — 3-minute graduation tripwire.

Lightweight Pulse-only scan designed for high-frequency cron execution.
Does NOT run full Oracle/Narrative pipeline — just:
1. Fetch Pulse bonded/bonding tokens from Mobula
2. Filter candidates (liquidity >$5k, volume >$1k)
3. Run Rug Warden on top candidates
4. Score with graduation profile
5. Output actionable candidates for the heartbeat agent

This runs every 3 minutes via OpenClaw cron, independent of the
10-minute full heartbeat cycle. The purpose is to catch PumpFun
graduations within their 2-5 minute entry window.

Usage:
    python3 -m lib.skills.pulse_quick_scan
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

# Reuse oracle's MobulaClient and Pulse parser
from lib.skills.oracle_query import MobulaClient, _parse_pulse_candidates
from lib.skills.warden_check import check_token
from lib.scoring import ConvictionScorer, SignalInput, detect_play_type
from lib.state import load_state


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[pulse-quick {ts}] {msg}", file=sys.stderr)


async def quick_scan() -> dict[str, Any]:
    """Run Pulse-only scan with Warden validation and graduation scoring."""
    t0 = time.monotonic()
    _log("Starting Pulse quick scan...")

    # Load configs
    firehose_path = os.path.join(os.path.dirname(__file__), '../../config/firehose.yaml')
    with open(firehose_path, 'r') as f:
        firehose = yaml.safe_load(f)

    mobula_config = firehose.get('mobula', {})
    if not mobula_config:
        return {"status": "ERROR", "error": "No Mobula config found", "candidates": []}

    pulse_url = mobula_config.get('pulse_url', '')
    pulse_endpoint = mobula_config.get('endpoints', {}).get('pulse', '/api/2/pulse')
    if not pulse_url:
        return {"status": "ERROR", "error": "No Pulse URL configured", "candidates": []}

    client = MobulaClient(mobula_config)

    # 1. Fetch Pulse data
    try:
        raw = client.get_pulse_listings(pulse_url, pulse_endpoint)
    except Exception as e:
        _log(f"Pulse fetch FAILED: {e}")
        return {"status": "ERROR", "error": str(e), "candidates": []}

    candidates = _parse_pulse_candidates(raw)
    _log(f"Pulse returned {len(candidates)} candidates after filters")

    if not candidates:
        elapsed = round(time.monotonic() - t0, 1)
        return {
            "status": "OK",
            "candidates": [],
            "pulse_raw": len(raw.get("data", raw).get("bonded", [])) + len(raw.get("data", raw).get("bonding", [])),
            "pulse_filtered": 0,
            "elapsed_s": elapsed,
        }

    # 2. Run Rug Warden on top 3 candidates (parallel)
    top_candidates = candidates[:3]
    warden_tasks = [check_token(c["token_mint"]) for c in top_candidates]
    warden_results = await asyncio.gather(*warden_tasks, return_exceptions=True)

    # 3. Score each candidate with graduation profile
    scorer = ConvictionScorer()
    state = load_state()
    scored = []

    for i, candidate in enumerate(top_candidates):
        # Get warden result
        if i < len(warden_results) and isinstance(warden_results[i], dict):
            warden = warden_results[i]
            warden_status = warden.get("verdict", "UNKNOWN")
            candidate["warden"] = warden
        else:
            warden_status = "UNKNOWN"
            candidate["warden"] = {"verdict": "UNKNOWN", "error": str(warden_results[i]) if i < len(warden_results) else "missing"}

        # Build signal input for graduation scoring
        signals = SignalInput(
            smart_money_whales=0,  # No whale data in quick scan
            narrative_volume_spike=0.0,  # No narrative data in quick scan
            narrative_kol_detected=False,
            narrative_age_minutes=0,
            rug_warden_status=warden_status,
            edge_bank_match_pct=0.0,
            pulse_ghost_metadata=candidate.get("pulse_ghost_metadata", False),
            pulse_organic_ratio=candidate.get("pulse_organic_ratio", 1.0),
            pulse_bundler_pct=candidate.get("pulse_bundler_pct", 0.0),
            pulse_sniper_pct=candidate.get("pulse_sniper_pct", 0.0),
            pulse_pro_trader_pct=candidate.get("pulse_pro_trader_pct", 0.0),
            pulse_deployer_migrations=candidate.get("pulse_deployer_migrations", 0),
            pulse_stage=candidate.get("pulse_stage", ""),
        )

        result = scorer.score(
            signals,
            pot_balance_sol=state.current_balance_sol or 14.0,
            sol_price_usd=state.sol_price_usd or 85.0,
        )

        candidate["score"] = {
            "play_type": result.play_type,
            "permission_score": result.permission_score,
            "ordering_score": result.ordering_score,
            "recommendation": result.recommendation,
            "breakdown": result.breakdown,
            "red_flags": result.red_flags,
            "primary_sources": result.primary_sources,
            "position_size_sol": round(result.position_size_sol, 4),
            "reasoning": result.reasoning,
        }

        scored.append(candidate)
        _log(f"  {candidate.get('token_symbol', '?')}: score={result.permission_score} "
             f"rec={result.recommendation} warden={warden_status}")

    # Sort by score descending
    scored.sort(key=lambda c: c["score"]["permission_score"], reverse=True)

    elapsed = round(time.monotonic() - t0, 1)
    _log(f"Quick scan done: {len(scored)} scored in {elapsed}s")

    # Count actionable candidates
    actionable = [c for c in scored if c["score"]["permission_score"] >= 60]

    return {
        "status": "OK",
        "candidates": scored,
        "actionable": len(actionable),
        "pulse_raw": len(raw.get("data", raw).get("bonded", [])) + len(raw.get("data", raw).get("bonding", [])),
        "pulse_filtered": len(candidates),
        "elapsed_s": elapsed,
    }


def main() -> None:
    result = asyncio.run(quick_scan())
    print(json.dumps(result, indent=2))

    # Exit 0 if OK, 1 if error
    sys.exit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
