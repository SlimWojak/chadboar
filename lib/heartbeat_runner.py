#!/usr/bin/env python3
"""
Heartbeat Runner — Execute full HEARTBEAT.md cycle with scoring integration.
This script is called by the agent to run steps 0-15 in a single execution.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv(override=True)

from lib.clients.birdeye import BirdeyeClient
from lib.clients.x_api import XClient
from lib.scoring import ConvictionScorer, SignalInput
from lib.utils.narrative_tracker import NarrativeTracker
from lib.utils.async_batch import batch_price_fetch
from lib.utils.file_lock import safe_read_json, safe_write_json
from lib.utils.red_flags import check_concentrated_volume
from lib.skills.warden_check import check_token
from lib.skills.oracle_query import query_oracle, _empty_flow_intel, _empty_buyer_depth
from lib.llm_utils import call_grok

import httpx


async def _send_s5_alert(
    symbol: str, mint: str, conflict: str, score
) -> None:
    """Send S5 arbitration alert to G via Telegram."""
    import os
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    if not token or not channel_id:
        return
    text = (
        f"⚖️ S5 ARBITRATION ALERT\n\n"
        f"Token: {symbol} ({mint[:12]}...)\n"
        f"Conflict: {conflict}\n"
        f"Scores: ordering={score.ordering_score}, "
        f"permission={score.permission_score}\n"
        f"Red flags: {score.red_flags}\n\n"
        f"Grok wanted TRADE → system downgraded to WATCHLIST.\n"
        f"Override? Send manual trade command if you disagree."
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": channel_id, "text": text},
            )
    except Exception:
        pass  # Best-effort alert


# Grok alpha override system prompt
GROK_ALPHA_PROMPT = """You are ChadBoar's alpha brain. DENSE YAML only.
Given signal data for a token, decide if this is alpha worth trading.
Rug Warden already PASSED — safety is cleared. Your job: pattern match.

Respond with EXACTLY this YAML format (no markdown fences):
verdict: TRADE | NOPE
reasoning: <one sentence — pattern match + conviction chain>
confidence: <0.0-1.0>

TRADE = upgrade to AUTO_EXECUTE. NOPE = stay on WATCHLIST.
Only say TRADE if you see genuine convergence (whale + narrative + volume).
Be ruthless. Most things are NOPE."""


async def run_heartbeat(timeout_seconds: float = 120.0) -> dict[str, Any]:
    """Execute full heartbeat cycle with time budget.
    
    Args:
        timeout_seconds: Maximum execution time before switching to observe-only mode
    
    Returns:
        Dict with cycle results, errors, and timeout flag
    """
    start_time = time.time()
    
    # Wrapper to check time budget
    def time_remaining() -> float:
        return timeout_seconds - (time.time() - start_time)
    
    # Load state with file locking (R5 fix)
    state_path = Path("state/state.json")
    state = safe_read_json(state_path)
    
    dry_run = state.get("dry_run_mode", True)
    cycle_num = state.get("dry_run_cycles_completed", 0) + 1
    
    result = {
        "cycle": cycle_num,
        "timestamp": datetime.utcnow().isoformat(),
        "dry_run": dry_run,
        "opportunities": [],
        "decisions": [],
        "errors": [],
        "exits": [],
        "timeout_triggered": False,
        "observe_only": False,
        "data_completeness": 1.0,
        "sources_failed": [],
    }
    
    # Check time budget before starting
    if time_remaining() < 10:
        result["timeout_triggered"] = True
        result["observe_only"] = True
        result["errors"].append(f"Time budget exhausted before start: {time_remaining():.1f}s remaining")
        return result
    
    # Step 7: Position Watchdog (runs before new signals to handle exits first)
    if time_remaining() < 10:
        result["timeout_triggered"] = True
        result["observe_only"] = True
        result["errors"].append("Timeout before watchdog step")
        return result
    
    birdeye_watchdog = BirdeyeClient()
    try:
        exit_decisions = await asyncio.wait_for(
            run_position_watchdog(state, birdeye_watchdog),
            timeout=min(30, time_remaining())
        )
        result["exits"] = exit_decisions
        # TODO: Execute exits in non-dry-run mode
    except asyncio.TimeoutError:
        result["errors"].append("Watchdog step timeout")
        result["timeout_triggered"] = True
        result["observe_only"] = True
    except Exception as e:
        result["errors"].append(f"Watchdog error: {e}")
    finally:
        await birdeye_watchdog.close()
    
    # Step 5: Smart Money Oracle (TGM pipeline)
    if time_remaining() < 10:
        result["timeout_triggered"] = True
        result["observe_only"] = True
        result["errors"].append("Timeout before oracle step")
        return result

    oracle_failed = False
    try:
        oracle_result = await asyncio.wait_for(
            query_oracle(),
            timeout=min(45, time_remaining())
        )
        if oracle_result.get("status") == "OK":
            oracle_signals = oracle_result.get("nansen_signals", [])
            result["oracle_signals"] = oracle_signals
            result["holdings_delta"] = oracle_result.get("holdings_delta", [])
            result["phase_timing"] = oracle_result.get("phase_timing", {})
            result["oracle_diagnostics"] = oracle_result.get("diagnostics", [])

            # Extract Mobula whale token candidates into scoring loop
            mobula_signals = oracle_result.get("mobula_signals", [])
            existing_mints = {s.get("token_mint") for s in oracle_signals}
            for ms in mobula_signals:
                if ms.get("token_mint") and ms["token_mint"] not in existing_mints:
                    oracle_signals.append({
                        "token_mint": ms["token_mint"],
                        "token_symbol": ms.get("token_symbol", "UNKNOWN"),
                        "wallet_count": 1,
                        "total_buy_usd": ms.get("accum_24h_usd", 0),
                        "confidence": ms.get("signal_strength", "low"),
                        "source": "mobula",
                        "flow_intel": _empty_flow_intel(),
                        "buyer_depth": _empty_buyer_depth(),
                        "dca_count": 0,
                        "discovery_source": "mobula-whale",
                    })
                    existing_mints.add(ms["token_mint"])
        else:
            oracle_signals = []
            oracle_failed = True
            result["sources_failed"].append("oracle")
            result["errors"].append(f"Oracle error: {oracle_result.get('error', 'unknown')}")
    except asyncio.TimeoutError:
        result["errors"].append("Oracle step timeout")
        oracle_signals = []
        oracle_failed = True
        result["sources_failed"].append("oracle")
    except Exception as e:
        result["errors"].append(f"Oracle error: {e}")
        oracle_signals = []
        oracle_failed = True
        result["sources_failed"].append("oracle")
    
    # Step 6: Narrative Hunter
    narrative_failed = False
    birdeye = BirdeyeClient()
    x_client = XClient()
    narrative_tracker = NarrativeTracker()
    
    try:
        # Get trending tokens
        trending = await birdeye.get_token_list_trending(limit=10)
        tokens = trending.get("data", trending.get("items", []))
        
        narrative_signals = []
        for token_data in (tokens[:5] if isinstance(tokens, list) else []):
            mint = token_data.get("address", "")
            if not mint:
                continue
            
            # Scan narrative for this token
            signal = await scan_token_narrative(mint, birdeye, x_client, narrative_tracker)
            if signal:
                narrative_signals.append(signal)
        
        result["narrative_signals"] = narrative_signals
    except Exception as e:
        result["errors"].append(f"Narrative error: {e}")
        narrative_signals = []
        narrative_failed = True
        result["sources_failed"].append("narrative")
    finally:
        await birdeye.close()
        await x_client.close()
    
    # PARTIAL DATA PENALTY (A2): Calculate data completeness
    sources_failed_count = len(result["sources_failed"])
    
    if sources_failed_count >= 2:
        # ≥2 primary sources unavailable → OBSERVE-ONLY MODE
        result["observe_only"] = True
        result["data_completeness"] = 0.0
        result["decisions"].append("OBSERVE-ONLY MODE: ≥2 primary sources failed (oracle, narrative)")
        # Skip entry logic, return early after watchdog
        return result
    elif oracle_failed:
        # Oracle missing → 0.7x penalty (30% reduction)
        result["data_completeness"] = 0.7
    elif narrative_failed:
        # Narrative missing → 0.8x penalty (20% reduction)
        result["data_completeness"] = 0.8
    else:
        # All sources available
        result["data_completeness"] = 1.0
    
    # Step 9: Conviction Scoring
    scorer = ConvictionScorer()
    
    # Merge signals by token mint
    all_mints = set()
    for sig in oracle_signals:
        all_mints.add(sig["token_mint"])
    for sig in narrative_signals:
        all_mints.add(sig["token_mint"])
    
    # Create new Birdeye client for red flag checks
    birdeye_red_flags = BirdeyeClient()
    
    for mint in all_mints:
        # Gather inputs
        oracle_sig = next((s for s in oracle_signals if s["token_mint"] == mint), None)
        narrative_sig = next((s for s in narrative_signals if s["token_mint"] == mint), None)
        
        # Use buyer_depth.smart_money_buyers for more accurate whale count when available
        if oracle_sig and oracle_sig.get("buyer_depth", {}).get("smart_money_buyers", 0) > 0:
            whales = oracle_sig["buyer_depth"]["smart_money_buyers"]
        else:
            whales = oracle_sig["wallet_count"] if oracle_sig else 0

        # Extract TGM flow intelligence fields
        flow_intel = (oracle_sig or {}).get("flow_intel", {})
        buyer_depth = (oracle_sig or {}).get("buyer_depth", {})
        exchange_outflow_usd = float(flow_intel.get("exchange_net_usd", 0))
        fresh_wallet_inflow_usd = float(flow_intel.get("fresh_wallet_net_usd", 0))
        smart_money_buy_vol = float(buyer_depth.get("total_buy_volume_usd", 0))
        dca_count = int((oracle_sig or {}).get("dca_count", 0))

        volume_spike = 0.0
        kol_detected = False
        age_minutes = 0

        if narrative_sig:
            volume_str = narrative_sig.get("volume_vs_avg", "0x")
            volume_spike = float(volume_str.replace("x", ""))
            kol_detected = narrative_sig.get("kol_mentions", 0) > 0
            age_minutes = narrative_tracker.get_age_minutes(mint)
        
        # Run Rug Warden
        rug_status = await run_rug_warden(mint)
        
        # RED FLAG CHECKS (Phase 3)
        concentrated_vol = False
        dumper_count = 0
        
        try:
            # Check concentrated volume
            trades_data = await birdeye_red_flags.get_trades(mint, limit=100)
            concentrated_vol, vol_reason = check_concentrated_volume(trades_data)
        except Exception as e:
            result["errors"].append(f"Volume concentration check failed for {mint[:8]}: {e}")
        
        # TODO: Dumper wallet check requires async wallet history fetching
        # For now, dumper_count = 0 (stub)
        
        # TIME MISMATCH CHECK (Phase 4 / B2)
        # Oracle accumulation detected + Narrative age <5min → too fast, suspicious
        time_mismatch_detected = (
            whales >= 3 and  # Oracle signal present
            volume_spike >= 5.0 and  # Narrative signal present
            age_minutes < 5  # Narrative is brand new
        )
        
        # Score
        signal_input = SignalInput(
            smart_money_whales=whales,
            narrative_volume_spike=volume_spike,
            narrative_kol_detected=kol_detected,
            narrative_age_minutes=age_minutes,
            rug_warden_status=rug_status,
            edge_bank_match_pct=0.0,  # No beads yet
            exchange_outflow_usd=exchange_outflow_usd,
            fresh_wallet_inflow_usd=fresh_wallet_inflow_usd,
            smart_money_buy_volume_usd=smart_money_buy_vol,
            dca_count=dca_count,
        )
        
        score = scorer.score(
            signal_input, 
            pot_balance_sol=state["current_balance_sol"],
            data_completeness=result["data_completeness"],
            concentrated_volume=concentrated_vol,
            dumper_wallet_count=dumper_count,
            time_mismatch=time_mismatch_detected,
        )
        
        opportunity = {
            "token_mint": mint,
            "token_symbol": (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN"),
            "ordering_score": score.ordering_score,
            "permission_score": score.permission_score,
            "breakdown": score.breakdown,
            "red_flags": score.red_flags,
            "primary_sources": score.primary_sources,
            "recommendation": score.recommendation,
            "position_size_sol": score.position_size_sol,
            "reasoning": score.reasoning,
            "signals": {
                "whales": whales,
                "volume_spike": volume_spike,
                "kol": kol_detected,
                "age_min": age_minutes,
                "rug": rug_status,
            }
        }
        
        # GROK ALPHA OVERRIDE (Step 9b)
        # After scoring, if WATCHLIST + rug warden PASS, ask Grok for alpha call.
        # Grok can upgrade WATCHLIST → AUTO_EXECUTE. Cannot override VETO.
        grok_override = None
        if score.recommendation == "WATCHLIST" and rug_status == "PASS":
            try:
                token_symbol = (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN")
                grok_prompt = (
                    f"Token: {token_symbol} ({mint[:12]}...)\n"
                    f"Signals: whales={whales}, volume_spike={volume_spike}x, "
                    f"kol={kol_detected}, age={age_minutes}min\n"
                    f"Score: ordering={score.ordering_score}, permission={score.permission_score}\n"
                    f"Primary sources: {score.primary_sources}\n"
                    f"Red flags: {score.red_flags}\n"
                    f"Reasoning: {score.reasoning}"
                )
                grok_result = await call_grok(
                    prompt=grok_prompt,
                    system_prompt=GROK_ALPHA_PROMPT,
                    max_tokens=256,
                    temperature=0.2,
                )
                if grok_result["status"] == "OK":
                    grok_content = grok_result["content"].strip()
                    grok_override = grok_content
                    # Parse TRADE/NOPE from Grok response
                    if "verdict: TRADE" in grok_content or "verdict:TRADE" in grok_content:
                        # Grok says TRADE — upgrade recommendation
                        score.recommendation = "AUTO_EXECUTE"
                        score.reasoning += f" | GROK OVERRIDE: {grok_content}"
                    else:
                        score.reasoning += f" | GROK: NOPE — staying WATCHLIST"
                else:
                    result["errors"].append(f"Grok override failed: {grok_result.get('error', 'unknown')}")
            except Exception as e:
                result["errors"].append(f"Grok override error for {mint[:8]}: {e}")

        # S5 ARBITRATION: Grok upgraded to AUTO_EXECUTE, but guards/flags conflict
        token_symbol = (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN")
        if (score.recommendation == "AUTO_EXECUTE"
                and grok_override
                and ("verdict: TRADE" in grok_override or "verdict:TRADE" in grok_override)):
            s5_conflict = None

            # Conflict 1: Divergence damping fired (no narrative backing)
            if 'divergence_damping' in score.red_flags:
                s5_conflict = (
                    f"S2 damping fired (no narrative) but Grok says TRADE "
                    f"for {token_symbol}"
                )

            # Conflict 2: Permission score too low despite Grok TRADE
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

        opportunity = {
            "token_mint": mint,
            "token_symbol": token_symbol,
            "ordering_score": score.ordering_score,
            "permission_score": score.permission_score,
            "breakdown": score.breakdown,
            "red_flags": score.red_flags,
            "primary_sources": score.primary_sources,
            "recommendation": score.recommendation,
            "position_size_sol": score.position_size_sol,
            "reasoning": score.reasoning,
            "grok_override": grok_override,
            "signals": {
                "whales": whales,
                "volume_spike": volume_spike,
                "kol": kol_detected,
                "age_min": age_minutes,
                "rug": rug_status,
            }
        }
        
        result["opportunities"].append(opportunity)
        
        # Decision logic
        if score.recommendation == "VETO":
            result["decisions"].append(f"🐗 VETO: {mint[:8]} — {score.reasoning}")
        elif score.recommendation == "DISCARD":
            result["decisions"].append(f"🐗 NOPE: {mint[:8]} — permission {score.permission_score} < 60")
        elif score.recommendation == "WATCHLIST":
            result["decisions"].append(f"🐗 WATCHLIST: {mint[:8]} — permission {score.permission_score} (60-84), ordering {score.ordering_score}, primary {len(score.primary_sources)}")
        elif score.recommendation == "AUTO_EXECUTE":
            if dry_run:
                result["decisions"].append(
                    f"🐗🔥 DRY-RUN TRADE: {mint[:8]} — would YOLO {score.position_size_sol:.4f} SOL "
                    f"(permission {score.permission_score}, ordering {score.ordering_score}, "
                    f"primary {len(score.primary_sources)}) OINK!"
                )
            else:
                result["decisions"].append(
                    f"🐗🔥 EXECUTE: {mint[:8]} — {score.position_size_sol:.4f} SOL "
                    f"(permission {score.permission_score}, ordering {score.ordering_score}) OINK!"
                )
                # TODO: Call execute_swap here in live mode
    
    # Close red flag client after loop
    await birdeye_red_flags.close()
    
    # Step 13: Update state with file locking (R5 fix)
    if dry_run:
        state["dry_run_cycles_completed"] = cycle_num
    state["last_heartbeat_time"] = datetime.utcnow().isoformat()
    
    safe_write_json(state_path, state)
    
    result["state_updated"] = True
    result["next_cycle"] = cycle_num + 1
    
    return result


async def run_position_watchdog(
    state: dict[str, Any],
    birdeye: BirdeyeClient,
) -> list[dict[str, Any]]:
    """Monitor open positions and generate exit decisions.
    
    Returns list of exit decisions with reason and percentage.
    """
    exit_decisions = []
    positions = state.get("positions", [])
    
    if not positions:
        return exit_decisions
    
    # Batch fetch all position prices (R4 fix: parallel API calls)
    mints = [pos["token_mint"] for pos in positions]
    price_data = await batch_price_fetch(birdeye, mints, max_concurrent=3)
    
    for pos in positions:
        mint = pos["token_mint"]
        entry_price = pos["entry_price"]
        entry_sol = pos["entry_amount_sol"]
        peak_price = pos.get("peak_price", entry_price)
        entry_time = datetime.fromisoformat(pos["entry_time"])
        
        # Get refreshed price from batch fetch
        overview = price_data.get(mint, {})
        data = overview.get("data", overview)
        
        if not data:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": "Price fetch failed",
                "exit_pct": 100,
                "urgency": "high",
            })
            continue
        
        current_price = float(data.get("price", 0))
        liquidity = float(data.get("liquidity", 0))
        
        # Update peak price if needed
        if current_price > peak_price:
            pos["peak_price"] = current_price
            peak_price = current_price
        
        # Calculate PnL
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        peak_drawdown_pct = ((current_price - peak_price) / peak_price) * 100
        
        # Position age
        age_minutes = (datetime.utcnow() - entry_time).total_seconds() / 60
        
        # Exit logic
        # 1. Stop-loss (-20%)
        if pnl_pct <= -20:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"Stop-loss hit: {pnl_pct:.1f}%",
                "exit_pct": 100,
                "urgency": "critical",
            })
        # 2. Take-profit tier 1 (+100%)
        elif pnl_pct >= 100 and not pos.get("tier1_exited", False):
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"TP tier 1: {pnl_pct:.1f}% (2x)",
                "exit_pct": 50,
                "urgency": "normal",
            })
            pos["tier1_exited"] = True
        # 3. Take-profit tier 2 (+400%)
        elif pnl_pct >= 400 and not pos.get("tier2_exited", False):
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"TP tier 2: {pnl_pct:.1f}% (5x)",
                "exit_pct": 30,
                "urgency": "normal",
            })
            pos["tier2_exited"] = True
        # 4. Trailing stop (20% from peak while in profit)
        elif pnl_pct > 0 and peak_drawdown_pct <= -20:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"Trailing stop: {peak_drawdown_pct:.1f}% from peak",
                "exit_pct": 100,
                "urgency": "high",
            })
        # 5. Time decay (no movement after 60min)
        elif age_minutes >= 60 and abs(pnl_pct) < 5:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"Time decay: {age_minutes:.0f}min, {pnl_pct:.1f}% PnL",
                "exit_pct": 100,
                "urgency": "low",
            })
        # 6. Liquidity drop (>50% from entry)
        elif pos.get("entry_liquidity") and liquidity < pos["entry_liquidity"] * 0.5:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"Liquidity drop: ${liquidity:,.0f} (was ${pos['entry_liquidity']:,.0f})",
                "exit_pct": 100,
                "urgency": "high",
            })
    
    return exit_decisions


async def run_rug_warden(mint: str) -> str:
    """Run Rug Warden check on a token mint."""
    try:
        result = await check_token(mint)
        return result.get("verdict", "FAIL")
    except Exception as e:
        # On error, return FAIL to be safe
        return "FAIL"


async def scan_token_narrative(
    mint: str,
    birdeye: BirdeyeClient,
    x_client: XClient,
    tracker: NarrativeTracker,
) -> dict[str, Any] | None:
    """Scan single token for narrative signals."""
    try:
        overview = await birdeye.get_token_overview(mint)
        data = overview.get("data", overview)
        symbol = data.get("symbol", "UNKNOWN")
        
        volume_1h = float(data.get("v1hUSD", 0))
        volume_24h = float(data.get("v24hUSD", 0))
        avg_hourly = volume_24h / 24 if volume_24h > 0 else 0
        volume_ratio = round(volume_1h / avg_hourly, 1) if avg_hourly > 0 else 0
        
        # Only track if volume spike detected
        if volume_ratio >= 5.0:
            tracker.record_detection(mint)
        
        x_data = await x_client.search_recent(f"${symbol} OR {symbol} solana", max_results=50)
        tweets = x_data.get("data", [])
        mention_count = len(tweets) if isinstance(tweets, list) else 0
        
        kol_count = 0
        users = {}
        for u in x_data.get("includes", {}).get("users", []):
            users[u.get("id")] = u
        if isinstance(tweets, list):
            for tweet in tweets:
                author = users.get(tweet.get("author_id", ""), {})
                followers = author.get("public_metrics", {}).get("followers_count", 0)
                if followers >= 10000:
                    kol_count += 1
        
        return {
            "token_mint": mint,
            "token_symbol": symbol,
            "x_mentions_1h": mention_count,
            "kol_mentions": kol_count,
            "volume_vs_avg": f"{volume_ratio}x",
        }
    except Exception:
        return None


async def main():
    result = await run_heartbeat()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
