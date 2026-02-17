"""Pulse Quick Scanner — 3-minute graduation tripwire + scalp execution.

Lightweight Pulse-only scan designed for high-frequency cron execution.
Does NOT run full Oracle/Narrative pipeline — just:
1. Check open scalp positions for exit triggers (TP/SL/time decay)
2. Fetch Pulse bonded/bonding tokens from Mobula (primary)
3. If Mobula returns 0: fallback to DexScreener free API
4. Filter candidates (liquidity >$5k, volume >$1k)
5. Run Rug Warden on top candidates
6. Score with graduation profile
7. Execute scalp entries for AUTO_EXECUTE candidates (sub-$50K mcap)

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
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

# Reuse oracle's MobulaClient and Pulse parser
from lib.skills.oracle_query import MobulaClient, _parse_pulse_candidates
from lib.skills.warden_check import check_token
from lib.scoring import ConvictionScorer, SignalInput, detect_play_type
from lib.state import load_state
from lib.clients.dexscreener import DexScreenerClient, map_dexscreener_to_candidate
from lib.clients.birdeye import BirdeyeClient
from lib.skills.execute_swap import execute_swap
from lib.skills.bead_write import write_bead
from lib.chain.anchor import get_wallet_pubkey
from lib.utils.file_lock import safe_read_json, safe_write_json
from lib.utils.async_batch import batch_price_fetch

WORKSPACE = Path(__file__).resolve().parent.parent.parent
STATE_PATH = WORKSPACE / "state" / "state.json"
RISK_PATH = WORKSPACE / "config" / "risk.yaml"


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[pulse-quick {ts}] {msg}", file=sys.stderr)


def _load_risk_config() -> dict[str, Any]:
    """Load risk.yaml configuration."""
    with open(RISK_PATH, "r") as f:
        return yaml.safe_load(f)


# ── Position monitoring (exit triggers) ─────────────────────────────


async def _check_open_positions() -> list[dict[str, Any]]:
    """Check open graduation positions for exit triggers.

    Runs BEFORE scanning new candidates. For each open graduation
    position, fetches current price and checks:
    - Take profit: +20% from entry
    - Stop loss: -15% from entry
    - Time decay: 15min with less than +5% gain

    Returns list of exit actions taken.
    """
    risk = _load_risk_config()
    scalp_cfg = risk.get("scalp", {})
    if not scalp_cfg.get("enabled", False):
        return []

    state = safe_read_json(STATE_PATH)
    positions = state.get("positions", [])

    # Filter graduation positions only
    grad_positions = [p for p in positions if p.get("play_type") == "graduation"]
    if not grad_positions:
        return []

    _log(f"Checking {len(grad_positions)} open graduation position(s)...")

    tp_pct = scalp_cfg.get("take_profit_pct", 20)
    sl_pct = scalp_cfg.get("stop_loss_pct", 15)
    decay_min = scalp_cfg.get("time_decay_minutes", 15)
    slippage = scalp_cfg.get("slippage_bps", 500)
    dry_run = state.get("dry_run_mode", True)

    birdeye = BirdeyeClient()
    exits: list[dict[str, Any]] = []

    try:
        # Batch fetch prices for all graduation positions
        mints = [p["token_mint"] for p in grad_positions]
        price_data = await batch_price_fetch(birdeye, mints, max_concurrent=3)

        for pos in grad_positions:
            mint = pos["token_mint"]
            symbol = pos.get("token_symbol", mint[:8])
            entry_price = pos.get("entry_price", 0)
            if entry_price <= 0:
                continue

            peak_price = pos.get("peak_price", entry_price)
            try:
                entry_time = datetime.fromisoformat(pos["entry_time"])
            except (KeyError, ValueError):
                continue

            # Get current price from batch fetch
            overview = price_data.get(mint, {})
            data = overview.get("data", overview)
            if not data:
                _log(f"  {symbol}: price fetch failed — skipping")
                continue

            current_price = float(data.get("price", 0))
            if current_price <= 0:
                continue

            # Update peak price
            if current_price > peak_price:
                pos["peak_price"] = current_price
                peak_price = current_price

            # Calculate PnL and age
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            age_minutes = (datetime.utcnow() - entry_time).total_seconds() / 60

            # Check exit triggers
            exit_reason = None
            if pnl_pct >= tp_pct:
                exit_reason = f"SCALP_TP: +{pnl_pct:.1f}% (target: +{tp_pct}%)"
            elif pnl_pct <= -sl_pct:
                exit_reason = f"SCALP_SL: {pnl_pct:.1f}% (limit: -{sl_pct}%)"
            elif age_minutes >= decay_min and pnl_pct < 5:
                exit_reason = (
                    f"SCALP_DECAY: {age_minutes:.0f}min, only {pnl_pct:+.1f}% "
                    f"(need +5% within {decay_min}min)"
                )

            if not exit_reason:
                _log(f"  {symbol}: PnL {pnl_pct:+.1f}%, age {age_minutes:.0f}min — hold")
                continue

            _log(f"  EXIT {symbol}: {exit_reason}")

            # Execute sell
            token_amount = pos.get("entry_amount_tokens", 0)
            sell_result: dict[str, Any] = {"status": "SKIP", "reason": "no token amount"}

            if token_amount > 0:
                wallet_pubkey = ""
                if not dry_run:
                    try:
                        wallet_pubkey = get_wallet_pubkey()
                    except Exception as e:
                        _log(f"  Wallet pubkey error: {e}")

                sell_result = await execute_swap(
                    direction="sell",
                    token_mint=mint,
                    amount=token_amount,
                    dry_run=dry_run,
                    slippage_bps=slippage,
                    wallet_pubkey=wallet_pubkey,
                )
                _log(f"  Sell result: {sell_result.get('status')}")
            else:
                _log(f"  No token amount for {symbol} — recording exit without sell")

            # Determine win/loss
            is_win = pnl_pct > 0

            # Update state atomically (re-read for freshness)
            state = safe_read_json(STATE_PATH)
            positions_current = state.get("positions", [])
            state["positions"] = [
                p for p in positions_current if p.get("token_mint") != mint
            ]

            # Return SOL adjusted for PnL
            entry_sol = pos.get("entry_amount_sol", 0)
            sol_returned = entry_sol * (1 + pnl_pct / 100)
            state["current_balance_sol"] = (
                state.get("current_balance_sol", 0) + sol_returned
            )

            # Update win/loss stats
            state["total_trades"] = state.get("total_trades", 0) + 1
            if is_win:
                state["total_wins"] = state.get("total_wins", 0) + 1
                state["consecutive_losses"] = 0
            else:
                state["total_losses"] = state.get("total_losses", 0) + 1
                state["consecutive_losses"] = (
                    state.get("consecutive_losses", 0) + 1
                )
                # Track daily loss as fraction of portfolio
                bal = max(state.get("current_balance_sol", 1), 0.01)
                loss_contribution = abs(pnl_pct / 100) * entry_sol / bal * 100
                state["daily_loss_pct"] = (
                    state.get("daily_loss_pct", 0) + loss_contribution
                )

            state["last_trade_time"] = datetime.utcnow().isoformat()
            safe_write_json(STATE_PATH, state)

            # Write exit bead
            try:
                write_bead("exit", {
                    "token_mint": mint,
                    "token_symbol": symbol,
                    "direction": "sell",
                    "amount_sol": entry_sol,
                    "price_usd": current_price,
                    "outcome": "win" if is_win else "loss",
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": exit_reason,
                    "signals": ["scalp_exit"],
                })
            except Exception as e:
                _log(f"  Bead write error: {e}")

            exits.append({
                "token_mint": mint,
                "symbol": symbol,
                "exit_reason": exit_reason,
                "pnl_pct": round(pnl_pct, 2),
                "sell_status": sell_result.get("status", "UNKNOWN"),
                "dry_run": dry_run,
            })

    finally:
        await birdeye.close()

    return exits


# ── Scalp entry execution ───────────────────────────────────────────


async def _execute_scalp_entry(
    scored_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Execute scalp entries for AUTO_EXECUTE graduation candidates.

    Guards checked before any trade:
    - state.halted -> skip
    - state.dry_run_mode -> paper trade only
    - daily_exposure_sol / current_balance_sol >= 0.30 -> blocked
    - graduation positions >= scalp.max_concurrent -> blocked
    - daily_graduation_count >= max_daily_plays -> blocked
    - candidate.market_cap_usd > scalp.max_mcap_usd -> skip
    - drawdown below halt threshold -> blocked

    Returns list of entry actions taken.
    """
    risk = _load_risk_config()
    scalp_cfg = risk.get("scalp", {})
    if not scalp_cfg.get("enabled", False):
        return []

    # Re-read state for freshness (concurrent safety)
    state = safe_read_json(STATE_PATH)

    # ── Guard checks ────────────────────────────────────────────
    if state.get("halted", False):
        _log("  Scalp entry BLOCKED: system halted")
        return []

    current_bal = state.get("current_balance_sol", 0)
    if current_bal <= 0:
        _log("  Scalp entry BLOCKED: zero balance")
        return []

    # Daily exposure check
    exposure_ratio = state.get("daily_exposure_sol", 0) / current_bal
    if exposure_ratio >= 0.30:
        _log(f"  Scalp entry BLOCKED: daily exposure {exposure_ratio:.0%} >= 30%")
        return []

    # Position count check
    positions = state.get("positions", [])
    grad_positions = [p for p in positions if p.get("play_type") == "graduation"]
    max_concurrent = scalp_cfg.get("max_concurrent", 3)
    if len(grad_positions) >= max_concurrent:
        _log(
            f"  Scalp entry BLOCKED: {len(grad_positions)}/{max_concurrent} "
            f"scalp positions open"
        )
        return []

    # Daily graduation count
    daily_grad_count = int(state.get("daily_graduation_count", 0))
    max_daily = (
        risk.get("conviction", {}).get("graduation", {}).get("max_daily_plays", 8)
    )
    if daily_grad_count >= max_daily:
        _log(
            f"  Scalp entry BLOCKED: daily graduation limit "
            f"{daily_grad_count}/{max_daily}"
        )
        return []

    # Drawdown check
    starting_bal = state.get("starting_balance_sol", 0)
    if starting_bal > 0:
        drawdown_halt_pct = risk.get("portfolio", {}).get("drawdown_halt_pct", 50)
        if current_bal / starting_bal < (1 - drawdown_halt_pct / 100):
            _log("  Scalp entry BLOCKED: drawdown halt triggered")
            return []

    # ── Execute entries ─────────────────────────────────────────
    entries: list[dict[str, Any]] = []
    dry_run = state.get("dry_run_mode", True)
    sol_price = state.get("sol_price_usd", 85.0)
    max_position_usd = scalp_cfg.get("max_position_usd", 10)
    max_mcap = scalp_cfg.get("max_mcap_usd", 50000)
    slippage = scalp_cfg.get("slippage_bps", 500)

    for candidate in scored_candidates:
        score_data = candidate.get("score", {})
        if score_data.get("recommendation") != "AUTO_EXECUTE":
            continue

        # Mcap filter — only scalp sub-$50K tokens
        mcap = candidate.get("market_cap_usd", 0)
        if mcap > max_mcap:
            _log(
                f"  {candidate.get('token_symbol', '?')}: "
                f"mcap ${mcap:,.0f} > ${max_mcap:,.0f} — skip (not a scalp target)"
            )
            continue

        mint = candidate["token_mint"]

        # Skip if already in a position for this token
        if any(p.get("token_mint") == mint for p in positions):
            _log(f"  {candidate.get('token_symbol', '?')}: already in position — skip")
            continue

        # Check concurrent limit (may have added during this loop)
        if len(grad_positions) + len(entries) >= max_concurrent:
            _log("  Scalp entry BLOCKED: max concurrent reached")
            break

        # Calculate position size in SOL
        amount_sol = max_position_usd / sol_price if sol_price > 0 else 0
        if amount_sol <= 0:
            continue

        symbol = candidate.get("token_symbol", "?")
        _log(
            f"  SCALP ENTRY: {symbol} — "
            f"${max_position_usd} ({amount_sol:.4f} SOL), mcap=${mcap:,.0f}"
        )

        wallet_pubkey = ""
        if not dry_run:
            try:
                wallet_pubkey = get_wallet_pubkey()
            except Exception as e:
                _log(f"  Wallet pubkey error: {e}")
                continue

        buy_result = await execute_swap(
            direction="buy",
            token_mint=mint,
            amount=amount_sol,
            dry_run=dry_run,
            slippage_bps=slippage,
            wallet_pubkey=wallet_pubkey,
        )

        status = buy_result.get("status")
        _log(f"  Buy result: {status}")

        if status not in ("SUCCESS", "DRY_RUN"):
            _log(f"  Buy FAILED: {buy_result.get('error', 'unknown')}")
            continue

        # Calculate entry price and token amount from swap result
        amount_out = float(buy_result.get("amount_out", 0))
        entry_price = 0.0
        if amount_out > 0:
            amount_in_sol = float(buy_result.get("amount_in", 0)) / 1e9
            if amount_in_sol > 0:
                entry_price = (amount_in_sol * sol_price) / amount_out

        # For dry run, estimate from candidate data
        if status == "DRY_RUN" and amount_out == 0:
            candidate_price = candidate.get("price_usd", 0)
            if candidate_price > 0:
                amount_out = (amount_sol * sol_price) / candidate_price
                entry_price = candidate_price

        now = datetime.utcnow().isoformat()
        new_position = {
            "token_mint": mint,
            "token_symbol": symbol,
            "direction": "long",
            "entry_price": entry_price,
            "entry_amount_sol": amount_sol,
            "entry_amount_tokens": amount_out,
            "entry_time": now,
            "peak_price": entry_price,
            "play_type": "graduation",
            "entry_market_cap_usd": mcap,
            "entry_liquidity_usd": candidate.get("liquidity_usd", 0),
            "thesis": (
                f"Scalp: pulse score {score_data.get('permission_score', 0)}, "
                f"mcap ${mcap:,.0f}"
            ),
            "signals": score_data.get("primary_sources", []),
        }

        # Update state atomically (re-read for freshness)
        state = safe_read_json(STATE_PATH)
        state.setdefault("positions", []).append(new_position)
        state["daily_exposure_sol"] = (
            state.get("daily_exposure_sol", 0) + amount_sol
        )
        state["daily_graduation_count"] = (
            int(state.get("daily_graduation_count", 0)) + 1
        )
        state["current_balance_sol"] = (
            state.get("current_balance_sol", 0) - amount_sol
        )
        state["last_trade_time"] = now
        safe_write_json(STATE_PATH, state)

        # Write entry bead
        try:
            write_bead("entry", {
                "token_mint": mint,
                "token_symbol": symbol,
                "direction": "buy",
                "amount_sol": amount_sol,
                "price_usd": entry_price,
                "thesis": new_position["thesis"],
                "signals": new_position["signals"],
            })
        except Exception as e:
            _log(f"  Bead write error: {e}")

        entries.append({
            "token_mint": mint,
            "symbol": symbol,
            "amount_sol": round(amount_sol, 4),
            "amount_usd": round(max_position_usd, 2),
            "entry_price": entry_price,
            "tokens_received": amount_out,
            "buy_status": status,
            "dry_run": dry_run,
        })

    return entries


# ── Main scan flow ──────────────────────────────────────────────────


async def quick_scan() -> dict[str, Any]:
    """Run Pulse-only scan with Warden validation, graduation scoring, and scalp execution."""
    t0 = time.monotonic()
    _log("Starting Pulse quick scan...")

    # 1. Check open positions for exit triggers (every 3 min)
    scalp_exits = await _check_open_positions()
    if scalp_exits:
        _log(f"Processed {len(scalp_exits)} scalp exit(s)")

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

    # 2. Fetch Pulse data (Mobula primary, DexScreener fallback below)
    try:
        raw = client.get_pulse_listings(pulse_url, pulse_endpoint)
    except Exception as e:
        _log(f"Pulse fetch FAILED: {e} — will try DexScreener fallback")
        raw = {}

    candidates = _parse_pulse_candidates(raw)
    pulse_raw_count = 0
    if isinstance(raw, dict):
        for section in ("bonded", "bonding", "new"):
            s = raw.get(section, {})
            pulse_raw_count += len(s.get("data", [])) if isinstance(s, dict) else len(s) if isinstance(s, list) else 0
    _log(f"Pulse returned {len(candidates)} candidates after filters (raw: {pulse_raw_count})")

    # DexScreener fallback when Mobula Pulse returns 0 results
    discovery_source_label = "pulse"
    if not candidates:
        _log("Mobula Pulse empty — falling back to DexScreener...")
        dex_client = DexScreenerClient()
        try:
            dex_raw = await dex_client.get_solana_candidates_enriched()
            _log(f"DexScreener returned {len(dex_raw)} raw Solana candidates")
            for raw_candidate in dex_raw:
                mapped = map_dexscreener_to_candidate(raw_candidate)
                if mapped is not None:
                    candidates.append(mapped)
            _log(f"DexScreener: {len(candidates)} candidates after filters (liq>$5k, vol>$1k)")
            discovery_source_label = "dexscreener"
        except Exception as e:
            _log(f"DexScreener fallback FAILED: {e}")
        finally:
            await dex_client.close()

    if not candidates:
        elapsed = round(time.monotonic() - t0, 1)
        return {
            "status": "OK",
            "candidates": [],
            "pulse_raw": pulse_raw_count,
            "pulse_filtered": 0,
            "discovery_source": discovery_source_label,
            "elapsed_s": elapsed,
            "scalp_exits": scalp_exits,
            "scalp_entries": [],
        }

    # 3. Run Rug Warden on top 3 candidates (parallel)
    # Pass play_type=graduation and pre-fetched liquidity so warden uses right thresholds
    top_candidates = candidates[:3]
    warden_tasks = [
        check_token(
            c["token_mint"],
            play_type="graduation",
            pre_liquidity_usd=c.get("liquidity_usd", 0),
        )
        for c in top_candidates
    ]
    warden_results = await asyncio.gather(*warden_tasks, return_exceptions=True)

    # 4. Score each candidate with graduation profile
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

    # 5. Execute scalp entries for AUTO_EXECUTE candidates
    scalp_entries = await _execute_scalp_entry(scored)

    elapsed = round(time.monotonic() - t0, 1)
    _log(f"Quick scan done: {len(scored)} scored in {elapsed}s")

    # Count actionable candidates
    actionable = [c for c in scored if c["score"]["permission_score"] >= 60]

    return {
        "status": "OK",
        "candidates": scored,
        "actionable": len(actionable),
        "pulse_raw": pulse_raw_count,
        "pulse_filtered": len(candidates),
        "discovery_source": discovery_source_label,
        "elapsed_s": elapsed,
        "scalp_exits": scalp_exits,
        "scalp_entries": scalp_entries,
    }


def main() -> None:
    result = asyncio.run(quick_scan())
    print(json.dumps(result, indent=2))

    # Exit 0 if OK, 1 if error
    sys.exit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
