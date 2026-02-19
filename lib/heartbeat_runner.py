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
from lib.clients.dexscreener import DexScreenerClient
from lib.scoring import ConvictionScorer, SignalInput, detect_play_type
from lib.utils.narrative_tracker import NarrativeTracker
from lib.utils.async_batch import batch_price_fetch
from lib.utils.file_lock import safe_read_json, safe_write_json
from lib.utils.red_flags import check_concentrated_volume
from lib.skills.warden_check import check_token
from lib.skills.oracle_query import query_oracle, _empty_flow_intel, _empty_buyer_depth
from lib.skills.paper_trade import (
    _load_trades as _load_paper_trades,
    log_paper_trade,
    check_paper_trades,
    write_paper_bead,
    update_trade_bead_id,
)
from lib.llm_utils import call_grok

import httpx

# Structured bead chain — best-effort, never blocks pipeline
try:
    from lib.beads import (
        BeadChain, Bead, BeadHeader, BeadType, BeadEdges, BeadProvenance,
        VerdictPayload, TradePayload, HeartbeatPayload,
    )
    _BEADS_AVAILABLE = True
except ImportError:
    _BEADS_AVAILABLE = False


def build_health_line(result: dict[str, Any]) -> str:
    """Build per-source diagnostic line for heartbeat messages.

    Format: 📡 Nan:{cand}/{raw} | Bird:{spike}/{status} | DexS:{filt}/{raw} | Whl:{active}/{total} | Ppr:{open}
    DexS/Pls label is dynamic based on which pulse source was used.
    """
    oh = result.get("oracle_health", {})
    funnel = result.get("funnel", {})

    # Nansen: candidates / raw trades (or ERR)
    if oh.get("nansen_error"):
        nan_part = "Nan:0/ERR"
    else:
        nan_cand = oh.get("nansen_candidates", funnel.get("nansen_filtered", 0))
        nan_raw = oh.get("nansen_raw_trades", funnel.get("nansen_raw", 0))
        nan_part = f"Nan:{nan_cand}/{nan_raw}"

    # Narrative: spike count / source status
    dexs_status = result.get("dexscreener_status", "")
    birdeye_status = result.get("birdeye_status", "SKIP")
    spike_count = funnel.get("narrative_with_spike", 0)
    if dexs_status == "OK":
        bird_part = f"Nar:{spike_count}/DexS"
    elif birdeye_status not in ("SKIP", ""):
        bird_part = f"Nar:{spike_count}/Bird:{birdeye_status}"
    else:
        bird_part = f"Nar:{spike_count}/ERR"

    # Pulse/DexScreener: filtered / raw (dynamic label)
    pulse_source = oh.get("pulse_source", "dexscreener")
    pulse_label = "DexS" if pulse_source == "dexscreener" else "Pls"
    if oh.get("pulse_error") and not oh.get("pulse_filtered"):
        pulse_part = f"{pulse_label}:0/ERR"
    else:
        p_filt = oh.get("pulse_filtered", funnel.get("pulse_filtered", 0))
        p_raw = oh.get("pulse_raw", funnel.get("pulse_raw", 0))
        pulse_part = f"{pulse_label}:{p_filt}/{p_raw}"

    # Whale: active / total
    whl_active = oh.get("whale_active", 0)
    whl_total = oh.get("whale_total", 0)
    whl_part = f"Whl:{whl_active}/{whl_total}"

    # Paper trades: open count
    paper_open = result.get("paper_open", 0)
    ppr_part = f"Ppr:{paper_open}"

    return f"📡 {nan_part} | {bird_part} | {pulse_part} | {whl_part} | {ppr_part}"


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

    # Init structured bead chain (best-effort)
    bead_chain = None
    if _BEADS_AVAILABLE:
        try:
            bead_chain = BeadChain()
        except Exception:
            pass
    
    # Funnel diagnostics — tracks signal flow for flight recorder
    funnel = {
        "nansen_raw": 0,
        "nansen_filtered": 0,
        "mobula_raw": 0,
        "mobula_resolved": 0,
        "pulse_raw": 0,
        "pulse_filtered": 0,
        "narrative_raw": 0,
        "narrative_with_spike": 0,
        "reached_scorer": 0,
        "scored_discard": 0,
        "scored_paper_trade": 0,
        "scored_watchlist": 0,
        "scored_execute": 0,
        "scored_veto": 0,
    }

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
        "funnel": funnel,
    }
    
    # Check time budget before starting
    if time_remaining() < 10:
        result["timeout_triggered"] = True
        result["observe_only"] = True
        result["errors"].append(f"Time budget exhausted before start: {time_remaining():.1f}s remaining")
        return result
    
    # Step 1c: Chain Verification (Flight Recorder integrity check)
    try:
        from lib.chain.verify import verify_on_boot, send_tamper_alert
        chain_status = verify_on_boot()
        result["chain_status"] = chain_status["status"]
        if chain_status["status"] == "TAMPERED":
            await send_tamper_alert(chain_status["details"])
            result["errors"].append(f"CHAIN TAMPERED: {chain_status['details']}")
    except Exception as e:
        result["errors"].append(f"Chain verification error: {e}")

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
            result["oracle_health"] = oracle_result.get("source_health", {})

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

            # Extract Pulse candidates (Phase 0) into scoring loop
            pulse_signals = oracle_result.get("pulse_signals", [])
            for ps in pulse_signals:
                if ps.get("token_mint") and ps["token_mint"] not in existing_mints:
                    oracle_signals.append({
                        "token_mint": ps["token_mint"],
                        "token_symbol": ps.get("token_symbol", "UNKNOWN"),
                        "wallet_count": 0,
                        "total_buy_usd": ps.get("volume_usd", 0),
                        "confidence": ps.get("confidence", "low"),
                        "source": "pulse",
                        "flow_intel": _empty_flow_intel(),
                        "buyer_depth": _empty_buyer_depth(),
                        "dca_count": 0,
                        "discovery_source": ps.get("discovery_source", "pulse-bonded"),
                        "market_cap_usd": ps.get("market_cap_usd", 0.0),
                        # Preserve pulse-specific fields for scoring
                        "pulse_ghost_metadata": ps.get("pulse_ghost_metadata", False),
                        "pulse_organic_ratio": ps.get("pulse_organic_ratio", 1.0),
                        "pulse_bundler_pct": ps.get("pulse_bundler_pct", 0.0),
                        "pulse_sniper_pct": ps.get("pulse_sniper_pct", 0.0),
                        "pulse_pro_trader_pct": ps.get("pulse_pro_trader_pct", 0.0),
                        "pulse_deployer_migrations": ps.get("pulse_deployer_migrations", 0),
                        "pulse_stage": ps.get("pulse_stage", ""),
                        "pulse_trending_score": ps.get("pulse_trending_score", 0.0),
                        "pulse_dexscreener_boosted": ps.get("pulse_dexscreener_boosted", False),
                    })
                    existing_mints.add(ps["token_mint"])
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
    
    # Funnel: oracle source counts
    if not oracle_failed:
        nansen_sigs = oracle_result.get("nansen_signals", [])
        mobula_sigs = oracle_result.get("mobula_signals", [])
        pulse_sigs = oracle_result.get("pulse_signals", [])
        funnel["nansen_raw"] = len(nansen_sigs)
        funnel["nansen_filtered"] = len([s for s in nansen_sigs if s.get("wallet_count", 0) >= 1])
        funnel["mobula_raw"] = len(mobula_sigs)
        funnel["mobula_resolved"] = len([s for s in mobula_sigs if s.get("token_mint")])
        funnel["pulse_raw"] = len(pulse_sigs)
        funnel["pulse_filtered"] = len([s for s in pulse_sigs if s.get("token_mint")])

    # Step 6: Narrative Hunter (on-chain volume only — X API disabled)
    # Primary: DexScreener (free). Fallback: Birdeye (paid).
    narrative_failed = False
    narrative_tracker = NarrativeTracker()
    birdeye_status = "SKIP"
    dexscreener_status = "OK"
    narrative_signals = []

    # Primary: DexScreener — volume spikes from free API
    dexscreener_narrative = DexScreenerClient()
    try:
        dex_candidates = await dexscreener_narrative.get_solana_candidates()
        for raw in (dex_candidates[:20] if isinstance(dex_candidates, list) else []):
            mint = raw.get("tokenAddress", "")
            if not mint:
                continue
            symbol = raw.get("token_symbol", raw.get("token_name", "UNKNOWN"))
            vol_1h = float(raw.get("volume_1h", 0))
            vol_24h = float(raw.get("volume_24h", 0))
            avg_hourly = vol_24h / 24 if vol_24h > 0 else 0
            volume_ratio = round(vol_1h / avg_hourly, 1) if avg_hourly > 0 else 0

            if volume_ratio >= 2.0:
                narrative_tracker.record_detection(mint)

            narrative_signals.append({
                "token_mint": mint,
                "token_symbol": symbol,
                "x_mentions_1h": 0,
                "kol_mentions": 0,
                "volume_vs_avg": f"{volume_ratio}x",
            })

        result["narrative_signals"] = narrative_signals
    except Exception as e:
        dexscreener_status = "ERR"
        result["errors"].append(f"DexScreener narrative error: {e}")
        narrative_signals = []

        # Fallback: Birdeye (paid)
        birdeye = BirdeyeClient()
        try:
            new_pairs = await birdeye.get_new_pairs(limit=20)
            tokens = new_pairs.get("data", new_pairs.get("items", []))
            birdeye_status = "OK"

            for token_data in (tokens[:10] if isinstance(tokens, list) else []):
                mint = token_data.get("address", token_data.get("baseAddress", ""))
                if not mint:
                    continue
                signal = await scan_token_narrative(mint, birdeye, narrative_tracker)
                if signal:
                    narrative_signals.append(signal)

            result["narrative_signals"] = narrative_signals
        except httpx.HTTPStatusError as e:
            resp_body = ""
            try:
                resp_body = e.response.text[:500]
            except Exception:
                pass
            birdeye_status = str(e.response.status_code)
            result["errors"].append(f"Birdeye fallback error: {e} | body: {resp_body}")
            narrative_signals = []
            narrative_failed = True
            result["sources_failed"].append("narrative")
        except Exception as e:
            birdeye_status = "ERR"
            result["errors"].append(f"Birdeye fallback error: {e}")
            narrative_signals = []
            narrative_failed = True
            result["sources_failed"].append("narrative")
        finally:
            await birdeye.close()
    finally:
        await dexscreener_narrative.close()
    result["birdeye_status"] = birdeye_status
    result["dexscreener_status"] = dexscreener_status
    
    # Funnel: narrative counts
    funnel["narrative_raw"] = len(narrative_signals)
    funnel["narrative_with_spike"] = len([
        s for s in narrative_signals
        if float(s.get("volume_vs_avg", "0x").replace("x", "")) >= 2.0
    ])

    # PARTIAL DATA PENALTY (A2): Calculate data completeness
    sources_failed_count = len(result["sources_failed"])
    
    if sources_failed_count >= 2:
        # ≥2 primary sources unavailable → OBSERVE-ONLY MODE
        result["observe_only"] = True
        result["data_completeness"] = 0.0
        result["decisions"].append("OBSERVE-ONLY MODE: ≥2 primary sources failed (oracle, narrative)")
        # Skip entry logic, return early after watchdog
        try:
            result["paper_open"] = len([t for t in _load_paper_trades() if not t.get("closed")])
        except Exception:
            result["paper_open"] = 0
        result["health_line"] = build_health_line(result)
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

    # Get edge bank bead count for cold-start logic
    edge_bank_bead_count = 0
    try:
        from lib.chain.bead_chain import get_chain_stats
        chain_stats = get_chain_stats()
        edge_bank_bead_count = chain_stats.get("total_beads", 0)
    except Exception:
        pass  # Chain unavailable — edge bank stays disabled

    # Get SOL price from state
    sol_price_usd = float(state.get("sol_price_usd", 78.0))

    # Track graduation plays this cycle (for daily sublimit)
    daily_graduation_count = int(state.get("daily_graduation_count", 0))

    # Merge signals by token mint
    all_mints = set()
    for sig in oracle_signals:
        all_mints.add(sig["token_mint"])
    for sig in narrative_signals:
        all_mints.add(sig["token_mint"])
    
    # Create new Birdeye client for red flag checks
    birdeye_red_flags = BirdeyeClient()
    
    funnel["reached_scorer"] = len(all_mints)

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
        
        # Detect play type early so we can pass it to Rug Warden
        # (Need pulse fields to detect graduation vs accumulation)
        pre_play_type = detect_play_type(SignalInput(
            smart_money_whales=whales,
            pulse_organic_ratio=float((oracle_sig or {}).get("pulse_organic_ratio", 1.0)),
            pulse_ghost_metadata=(oracle_sig or {}).get("pulse_ghost_metadata", False),
            pulse_bundler_pct=float((oracle_sig or {}).get("pulse_bundler_pct", 0.0)),
            pulse_sniper_pct=float((oracle_sig or {}).get("pulse_sniper_pct", 0.0)),
            pulse_pro_trader_pct=float((oracle_sig or {}).get("pulse_pro_trader_pct", 0.0)),
            pulse_deployer_migrations=int((oracle_sig or {}).get("pulse_deployer_migrations", 0)),
        ))
        pre_liquidity = float((oracle_sig or {}).get("liquidity_usd", 0))

        # Run Rug Warden with play-type-aware thresholds
        rug_status = await run_rug_warden(mint, play_type=pre_play_type, pre_liquidity_usd=pre_liquidity or None)
        
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
        
        # Extract pulse-specific fields if this signal came from Pulse
        pulse_ghost = (oracle_sig or {}).get("pulse_ghost_metadata", False)
        pulse_organic = float((oracle_sig or {}).get("pulse_organic_ratio", 1.0))
        pulse_bundler = float((oracle_sig or {}).get("pulse_bundler_pct", 0.0))
        pulse_sniper = float((oracle_sig or {}).get("pulse_sniper_pct", 0.0))
        pulse_pro = float((oracle_sig or {}).get("pulse_pro_trader_pct", 0.0))
        pulse_deployer = int((oracle_sig or {}).get("pulse_deployer_migrations", 0))
        pulse_stage = (oracle_sig or {}).get("pulse_stage", "")
        pulse_trending = float((oracle_sig or {}).get("pulse_trending_score", 0.0))
        pulse_ds_boosted = bool((oracle_sig or {}).get("pulse_dexscreener_boosted", False))
        market_cap = float((oracle_sig or {}).get("market_cap_usd", 0.0))

        # Enrichment: Birdeye holder delta (single call per candidate, well within rate limits)
        holder_delta = 0.0
        try:
            holder_data = await birdeye_red_flags.get_holder_count(mint)
            h_data = holder_data.get("data", holder_data)
            if isinstance(h_data, dict):
                holder_delta = float(h_data.get("holder_change_24h_percent",
                                     h_data.get("holderChangePercent", 0)))
        except Exception as e:
            result["errors"].append(f"Holder delta fetch failed for {mint[:8]}: {e}")

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
            pulse_ghost_metadata=pulse_ghost,
            pulse_organic_ratio=pulse_organic,
            pulse_bundler_pct=pulse_bundler,
            pulse_sniper_pct=pulse_sniper,
            pulse_pro_trader_pct=pulse_pro,
            pulse_deployer_migrations=pulse_deployer,
            pulse_stage=pulse_stage,
            holder_delta_pct=holder_delta,
            entry_market_cap_usd=market_cap,
            pulse_trending_score=pulse_trending,
            pulse_dexscreener_boosted=pulse_ds_boosted,
        )
        
        score = scorer.score(
            signal_input,
            pot_balance_sol=state["current_balance_sol"],
            data_completeness=result["data_completeness"],
            concentrated_volume=concentrated_vol,
            dumper_wallet_count=dumper_count,
            time_mismatch=time_mismatch_detected,
            edge_bank_bead_count=edge_bank_bead_count,
            daily_graduation_count=daily_graduation_count,
            sol_price_usd=sol_price_usd,
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
            "play_type": score.play_type,
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
            },
            "enrichment": {
                "holder_delta_pct": holder_delta,
                "trending_score": pulse_trending,
                "dexscreener_boosted": pulse_ds_boosted,
                "entry_market_cap": market_cap,
            },
        }

        # Emit verdict bead (structured chain — best-effort)
        verdict_bead_id = ""
        if bead_chain:
            try:
                _rec = score.recommendation
                _wv = rug_status if rug_status in ("PASS", "WARN", "FAIL") else "UNKNOWN"
                verdict_bead = Bead(
                    header=BeadHeader(bead_type=BeadType.VERDICT),
                    edges=BeadEdges(edges_complete=False,
                                    edges_incomplete_reason="signal beads not yet emitted"),
                    provenance=BeadProvenance(
                        data_sources=result.get("source_health", {}),
                    ),
                    payload=VerdictPayload(
                        token_mint=mint,
                        token_symbol=opportunity["token_symbol"],
                        play_type=score.play_type,
                        scoring_breakdown=score.breakdown,
                        conviction_score=score.permission_score,
                        recommendation=_rec,
                        warden_verdict=_wv,
                        red_flags=score.red_flags if isinstance(score.red_flags, dict) else {"flags": score.red_flags},
                    ),
                )
                verdict_bead_id = bead_chain.write_bead(verdict_bead)
            except Exception:
                pass
        opportunity["verdict_bead_id"] = verdict_bead_id

        result["opportunities"].append(opportunity)

        # Funnel: track verdict counts
        if score.recommendation == "VETO":
            funnel["scored_veto"] += 1
        elif score.recommendation == "DISCARD":
            funnel["scored_discard"] += 1
        elif score.recommendation == "PAPER_TRADE":
            funnel["scored_paper_trade"] += 1
        elif score.recommendation == "WATCHLIST":
            funnel["scored_watchlist"] += 1
        elif score.recommendation == "AUTO_EXECUTE":
            funnel["scored_execute"] += 1

        # Decision logic
        if score.recommendation == "VETO":
            result["decisions"].append(f"🐗 VETO: {mint[:8]} — {score.reasoning}")
        elif score.recommendation == "DISCARD":
            result["decisions"].append(f"🐗 NOPE: {mint[:8]} — permission {score.permission_score} < {scorer.thresholds.get('paper_trade', 30)}")
        elif score.recommendation == "PAPER_TRADE":
            # Log phantom trade for calibration — builds beads without risk
            token_symbol = (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN")
            try:
                paper_candidate = {
                    "token_mint": mint,
                    "token_symbol": token_symbol,
                    "price_usd": float((oracle_sig or {}).get("market_cap_usd", 0)),
                    "liquidity_usd": float((oracle_sig or {}).get("liquidity_usd", 0)),
                    "volume_usd": float((oracle_sig or {}).get("volume_usd", (oracle_sig or {}).get("total_buy_usd", 0))),
                    "source": (oracle_sig or {}).get("source", "unknown"),
                    "discovery_source": (oracle_sig or {}).get("discovery_source", "unknown"),
                    "score": {
                        "play_type": score.play_type,
                        "permission_score": score.permission_score,
                        "ordering_score": score.ordering_score,
                        "recommendation": score.recommendation,
                        "breakdown": score.breakdown,
                        "red_flags": score.red_flags,
                    },
                    "warden": {"verdict": rug_status},
                    "verdict_bead_id": verdict_bead_id,
                }
                trade_record = log_paper_trade(paper_candidate)
                # Write analysis bead for warden-passing paper trades
                if rug_status in ("PASS", "WARN"):
                    try:
                        write_paper_bead(trade_record)
                    except Exception:
                        pass  # Best-effort bead writing
                # Emit structured trade bead
                if bead_chain and verdict_bead_id:
                    try:
                        _wv = rug_status if rug_status in ("PASS", "WARN", "FAIL") else "UNKNOWN"
                        trade_bead = Bead(
                            header=BeadHeader(bead_type=BeadType.TRADE),
                            edges=BeadEdges(derived_from=[verdict_bead_id]),
                            payload=TradePayload(
                                token_mint=mint,
                                token_symbol=token_symbol,
                                play_type=score.play_type,
                                scoring_breakdown=score.breakdown,
                                conviction_score=score.permission_score,
                                recommendation=score.recommendation,
                                warden_verdict=_wv,
                                red_flags=score.red_flags if isinstance(score.red_flags, dict) else {"flags": score.red_flags},
                                entry_price=trade_record.get("entry_price_fdv", 0),
                                entry_amount_sol=0.0,
                                gate="paper",
                            ),
                        )
                        trade_bead_id = bead_chain.write_bead(trade_bead)
                        update_trade_bead_id(trade_record["id"], trade_bead_id, verdict_bead_id)
                    except Exception:
                        pass
                result["decisions"].append(
                    f"🐗📝 PAPER: {token_symbol} ({mint[:8]}) — [{score.play_type}] "
                    f"permission {score.permission_score}, ordering {score.ordering_score}"
                )
            except Exception as e:
                result["errors"].append(f"Paper trade logging failed for {mint[:8]}: {e}")
                result["decisions"].append(
                    f"🐗📝 PAPER (log failed): {mint[:8]} — [{score.play_type}] "
                    f"permission {score.permission_score}"
                )
        elif score.recommendation == "WATCHLIST":
            # Also paper-trade WATCHLIST candidates to build beads faster
            token_symbol = (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN")
            try:
                paper_candidate = {
                    "token_mint": mint,
                    "token_symbol": token_symbol,
                    "price_usd": float((oracle_sig or {}).get("market_cap_usd", 0)),
                    "liquidity_usd": float((oracle_sig or {}).get("liquidity_usd", 0)),
                    "volume_usd": float((oracle_sig or {}).get("volume_usd", (oracle_sig or {}).get("total_buy_usd", 0))),
                    "source": (oracle_sig or {}).get("source", "unknown"),
                    "discovery_source": (oracle_sig or {}).get("discovery_source", "unknown"),
                    "score": {
                        "play_type": score.play_type,
                        "permission_score": score.permission_score,
                        "ordering_score": score.ordering_score,
                        "recommendation": score.recommendation,
                        "breakdown": score.breakdown,
                        "red_flags": score.red_flags,
                    },
                    "warden": {"verdict": rug_status},
                    "verdict_bead_id": verdict_bead_id,
                }
                trade_record = log_paper_trade(paper_candidate)
                # Write analysis bead for warden-passing watchlist trades
                if rug_status in ("PASS", "WARN"):
                    try:
                        write_paper_bead(trade_record)
                    except Exception:
                        pass  # Best-effort bead writing
                # Emit structured trade bead
                if bead_chain and verdict_bead_id:
                    try:
                        _wv = rug_status if rug_status in ("PASS", "WARN", "FAIL") else "UNKNOWN"
                        trade_bead = Bead(
                            header=BeadHeader(bead_type=BeadType.TRADE),
                            edges=BeadEdges(derived_from=[verdict_bead_id]),
                            payload=TradePayload(
                                token_mint=mint,
                                token_symbol=token_symbol,
                                play_type=score.play_type,
                                scoring_breakdown=score.breakdown,
                                conviction_score=score.permission_score,
                                recommendation=score.recommendation,
                                warden_verdict=_wv,
                                red_flags=score.red_flags if isinstance(score.red_flags, dict) else {"flags": score.red_flags},
                                entry_price=trade_record.get("entry_price_fdv", 0),
                                entry_amount_sol=0.0,
                                gate="paper",
                            ),
                        )
                        trade_bead_id = bead_chain.write_bead(trade_bead)
                        update_trade_bead_id(trade_record["id"], trade_bead_id, verdict_bead_id)
                    except Exception:
                        pass
            except Exception:
                pass  # Best-effort paper logging for watchlist
            result["decisions"].append(
                f"🐗 WATCHLIST+PAPER: {token_symbol} ({mint[:8]}) — [{score.play_type}] "
                f"permission {score.permission_score}, ordering {score.ordering_score}, "
                f"primary {len(score.primary_sources)}"
            )
        elif score.recommendation == "AUTO_EXECUTE":
            # Track graduation plays for daily sublimit
            if score.play_type == "graduation":
                daily_graduation_count += 1

            if dry_run:
                result["decisions"].append(
                    f"🐗🔥 DRY-RUN TRADE: {mint[:8]} — [{score.play_type}] would YOLO {score.position_size_sol:.4f} SOL "
                    f"(permission {score.permission_score}, ordering {score.ordering_score}, "
                    f"primary {len(score.primary_sources)}) OINK!"
                )
            else:
                result["decisions"].append(
                    f"🐗🔥 EXECUTE: {mint[:8]} — [{score.play_type}] {score.position_size_sol:.4f} SOL "
                    f"(permission {score.permission_score}, ordering {score.ordering_score}) OINK!"
                )
                # TODO: Call execute_swap here in live mode
    
    # Close red flag client after loop
    await birdeye_red_flags.close()

    # Step 12b: Check PnL on open paper trades (every cycle)
    try:
        pnl_result = await check_paper_trades(bead_chain=bead_chain)
        result["paper_pnl_checked"] = pnl_result.get("checked", 0)
    except Exception as e:
        result["errors"].append(f"Paper PnL check failed: {e}")
        result["paper_pnl_checked"] = 0

    # Step 13: Update state with file locking (R5 fix)
    if dry_run:
        state["dry_run_cycles_completed"] = cycle_num
    state["last_heartbeat_time"] = datetime.utcnow().isoformat()

    # Reset daily graduation count if date changed, otherwise persist
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("daily_date") != today:
        state["daily_graduation_count"] = 0
    else:
        state["daily_graduation_count"] = daily_graduation_count

    safe_write_json(state_path, state)

    # Append heartbeat chain bead (Flight Recorder — legacy)
    try:
        import hashlib
        from lib.chain.bead_chain import append_bead as chain_append
        state_json = json.dumps(state, sort_keys=True)
        state_hash = hashlib.sha256(state_json.encode()).hexdigest()
        chain_append("heartbeat", {
            "cycle": cycle_num,
            "opportunities": len(result["opportunities"]),
            "decisions": len(result["decisions"]),
            "exits": len(result["exits"]),
            "errors": result["errors"],
            "observe_only": result["observe_only"],
            "data_completeness": result["data_completeness"],
            "state_hash": state_hash,
            "funnel": funnel,
        })
    except Exception:
        pass  # Chain is best-effort

    # Structured heartbeat bead (new bead chain)
    if bead_chain:
        try:
            _source_health = {}
            oh = result.get("oracle_health", {})
            if oh.get("nansen_error"):
                _source_health["nansen"] = "ERR"
            else:
                _source_health["nansen"] = "OK"
            _source_health["dexscreener"] = oh.get("narrative_source", "OK")
            _source_health["birdeye"] = oh.get("birdeye_status", "SKIP")
            _source_health["whale"] = "OK" if oh.get("whale_count", 0) > 0 else "EMPTY"

            hb_bead = Bead(
                header=BeadHeader(bead_type=BeadType.HEARTBEAT),
                provenance=BeadProvenance(data_sources=_source_health),
                payload=HeartbeatPayload(
                    cycle_number=cycle_num,
                    signals_found=funnel.get("reached_scorer", 0),
                    signals_vetoed=funnel.get("scored_veto", 0),
                    signals_passed=funnel.get("scored_paper_trade", 0)
                                   + funnel.get("scored_watchlist", 0)
                                   + funnel.get("scored_execute", 0),
                    pot_sol=state.get("current_balance_sol", 0),
                    positions_count=len(state.get("positions", [])),
                    pipeline_health=_source_health,
                    canary_hash=hashlib.sha256(
                        json.dumps(state, sort_keys=True).encode()
                    ).hexdigest()[:12],
                ),
            )
            bead_chain.write_bead(hb_bead)
        except Exception:
            pass  # Best-effort

    result["state_updated"] = True
    result["next_cycle"] = cycle_num + 1

    # Execution canary — proof that heartbeat_runner.py actually ran.
    # Watchdog checks this file's mtime to detect hallucinated heartbeats.
    canary_path = Path("state/last_real_hb.txt")
    try:
        import hashlib as _hl
        _canary_hash = _hl.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:12]
        canary_path.write_text(
            f"{datetime.utcnow().isoformat()}|cycle={cycle_num + 1}|hash={_canary_hash}\n"
        )
    except Exception:
        pass  # Best-effort canary

    try:
        result["paper_open"] = len([t for t in _load_paper_trades() if not t.get("closed")])
    except Exception:
        result["paper_open"] = 0
    result["health_line"] = build_health_line(result)

    return result


def _get_mcap_exit_tier(entry_market_cap: float, play_type: str = "accumulation") -> dict:
    """Get market-cap-aware exit parameters.

    | MC Tier        | TP1 pnl% (sell%) | TP2 pnl% (sell%) | Trail% | Decay min | SL%  |
    |----------------|-------------------|-------------------|--------|-----------|------|
    | Micro  <100k   | 80  (40)          | 200 (40)          | 25     | 20        | -30  |
    | Small  100-500k| 60  (50)          | 150 (30)          | 20     | 30        | -25  |
    | Mid    500k-2M | 40  (50)          | 100 (30)          | 15     | 45        | -20  |
    | Large  >2M     | 30  (50)          | 60  (30)          | 12     | 60        | -15  |

    Graduation plays use tighter time decay (halved).
    """
    if entry_market_cap < 100_000:
        tier = {"tp1_pnl": 80, "tp1_sell": 40, "tp2_pnl": 200, "tp2_sell": 40,
                "trail_pct": 25, "decay_min": 20, "stop_loss": -30, "label": "micro"}
    elif entry_market_cap < 500_000:
        tier = {"tp1_pnl": 60, "tp1_sell": 50, "tp2_pnl": 150, "tp2_sell": 30,
                "trail_pct": 20, "decay_min": 30, "stop_loss": -25, "label": "small"}
    elif entry_market_cap < 2_000_000:
        tier = {"tp1_pnl": 40, "tp1_sell": 50, "tp2_pnl": 100, "tp2_sell": 30,
                "trail_pct": 15, "decay_min": 45, "stop_loss": -20, "label": "mid"}
    else:
        tier = {"tp1_pnl": 30, "tp1_sell": 50, "tp2_pnl": 60, "tp2_sell": 30,
                "trail_pct": 12, "decay_min": 60, "stop_loss": -15, "label": "large"}

    # Graduation plays are speed plays — tighter time decay
    if play_type == "graduation":
        tier["decay_min"] = max(10, tier["decay_min"] // 2)

    return tier


async def run_position_watchdog(
    state: dict[str, Any],
    birdeye: BirdeyeClient,
) -> list[dict[str, Any]]:
    """Monitor open positions and generate exit decisions.

    Uses market-cap-aware exit tiers: smaller MC tokens get wider TP
    targets (bigger moves are realistic) and tighter time decay.

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

        # Market-cap-aware exit tiers
        entry_mc = float(pos.get("entry_market_cap_usd", 0))
        pos_play_type = pos.get("play_type", "accumulation")
        tier = _get_mcap_exit_tier(entry_mc, pos_play_type)

        # Exit logic (mcap-aware)
        # 1. Stop-loss
        if pnl_pct <= tier["stop_loss"]:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"Stop-loss hit: {pnl_pct:.1f}% (tier={tier['label']}, sl={tier['stop_loss']}%)",
                "exit_pct": 100,
                "urgency": "critical",
            })
        # 2. Take-profit tier 1
        elif pnl_pct >= tier["tp1_pnl"] and not pos.get("tier1_exited", False):
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"TP tier 1: {pnl_pct:.1f}% (tier={tier['label']}, target={tier['tp1_pnl']}%)",
                "exit_pct": tier["tp1_sell"],
                "urgency": "normal",
            })
            pos["tier1_exited"] = True
        # 3. Take-profit tier 2
        elif pnl_pct >= tier["tp2_pnl"] and not pos.get("tier2_exited", False):
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"TP tier 2: {pnl_pct:.1f}% (tier={tier['label']}, target={tier['tp2_pnl']}%)",
                "exit_pct": tier["tp2_sell"],
                "urgency": "normal",
            })
            pos["tier2_exited"] = True
        # 4. Trailing stop (mcap-aware trail % from peak while in profit)
        elif pnl_pct > 0 and peak_drawdown_pct <= -tier["trail_pct"]:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"Trailing stop: {peak_drawdown_pct:.1f}% from peak (tier={tier['label']}, trail={tier['trail_pct']}%)",
                "exit_pct": 100,
                "urgency": "high",
            })
        # 5. Time decay (mcap-aware — no movement after N min)
        elif age_minutes >= tier["decay_min"] and abs(pnl_pct) < 5:
            exit_decisions.append({
                "token_mint": mint,
                "symbol": pos["token_symbol"],
                "reason": f"Time decay: {age_minutes:.0f}min, {pnl_pct:.1f}% PnL (tier={tier['label']}, limit={tier['decay_min']}min)",
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


async def run_rug_warden(
    mint: str,
    play_type: str = "accumulation",
    pre_liquidity_usd: float | None = None,
) -> str:
    """Run Rug Warden check on a token mint."""
    try:
        result = await check_token(mint, play_type=play_type, pre_liquidity_usd=pre_liquidity_usd)
        return result.get("verdict", "FAIL")
    except Exception as e:
        # On error, return FAIL to be safe
        return "FAIL"


async def scan_token_narrative(
    mint: str,
    birdeye: BirdeyeClient,
    tracker: NarrativeTracker,
) -> dict[str, Any] | None:
    """Scan single token for narrative signals (on-chain volume only).

    X API is disabled — KOL/social detection unavailable. Narrative
    signal is purely volume-spike-based from Birdeye on-chain data.
    """
    try:
        overview = await birdeye.get_token_overview(mint)
        data = overview.get("data", overview)
        symbol = data.get("symbol", "UNKNOWN")

        volume_1h = float(data.get("v1hUSD", 0))
        volume_24h = float(data.get("v24hUSD", 0))
        avg_hourly = volume_24h / 24 if volume_24h > 0 else 0
        volume_ratio = round(volume_1h / avg_hourly, 1) if avg_hourly > 0 else 0

        # Track if volume spike detected (lowered from 5x to 2x for gradient scoring)
        if volume_ratio >= 2.0:
            tracker.record_detection(mint)

        return {
            "token_mint": mint,
            "token_symbol": symbol,
            "x_mentions_1h": 0,
            "kol_mentions": 0,
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
