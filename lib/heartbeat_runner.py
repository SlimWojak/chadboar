#!/usr/bin/env python3
"""
Heartbeat Runner — Execute full HEARTBEAT.md cycle with scoring integration.
This script is called by the agent to run steps 0-15 in a single execution.

Decomposed into discrete stages with clear inputs/outputs per stage.
Each stage records its health in cycle_health for the HEARTBEAT bead.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
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

from lib.skills.execute_swap import execute_swap
from lib.chain.anchor import get_wallet_pubkey

# Structured bead chain (v0.2) — best-effort, never blocks pipeline
try:
    from lib.beads import BeadChain, BeadType, RejectionCategory
    from lib.beads.emitters import (
        emit_fact_bead,
        emit_signal_bead,
        emit_proposal_bead,
        emit_proposal_rejected_bead,
        emit_heartbeat_bead,
        emit_policy_bead,
        emit_model_version_bead,
        emit_claim_bead,
        emit_pipeline_error,
    )
    _BEADS_AVAILABLE = True
except ImportError:
    _BEADS_AVAILABLE = False
    emit_pipeline_error = None  # type: ignore[assignment]


def _record_error(
    bead_chain,
    stage: str,
    error: Exception,
    context: dict | None = None,
    cycle_start=None,
) -> None:
    """Record a pipeline error to the bead chain. Never raises."""
    if emit_pipeline_error is None:
        import sys
        print(f"[PIPELINE_ERROR] stage={stage} error={error}", file=sys.stderr)
        return
    try:
        from datetime import datetime, timezone
        emit_pipeline_error(
            bead_chain,
            stage=stage,
            error=error,
            context=context,
            cycle_start=cycle_start,
            cycle_end=datetime.now(timezone.utc),
        )
    except Exception:
        import sys
        print(f"[PIPELINE_ERROR] stage={stage} error={error}", file=sys.stderr)


def _stage_timer():
    """Returns a callable that records elapsed ms since creation."""
    _t0 = time.time()
    return lambda: int((time.time() - _t0) * 1000)


def build_health_line(result: dict[str, Any]) -> str:
    """Build per-source diagnostic line for heartbeat messages."""
    oh = result.get("oracle_health", {})
    funnel = result.get("funnel", {})

    if oh.get("nansen_error"):
        nan_part = "Nan:0/ERR"
    else:
        nan_cand = oh.get("nansen_candidates", funnel.get("nansen_filtered", 0))
        nan_raw = oh.get("nansen_raw_trades", funnel.get("nansen_raw", 0))
        nan_part = f"Nan:{nan_cand}/{nan_raw}"

    dexs_status = result.get("dexscreener_status", "")
    birdeye_status = result.get("birdeye_status", "SKIP")
    spike_count = funnel.get("narrative_with_spike", 0)
    if dexs_status == "OK":
        bird_part = f"Nar:{spike_count}/DexS"
    elif birdeye_status not in ("SKIP", ""):
        bird_part = f"Nar:{spike_count}/Bird:{birdeye_status}"
    else:
        bird_part = f"Nar:{spike_count}/ERR"

    pulse_source = oh.get("pulse_source", "dexscreener")
    pulse_label = "DexS" if pulse_source == "dexscreener" else "Pls"
    if oh.get("pulse_error") and not oh.get("pulse_filtered"):
        pulse_part = f"{pulse_label}:0/ERR"
    else:
        p_filt = oh.get("pulse_filtered", funnel.get("pulse_filtered", 0))
        p_raw = oh.get("pulse_raw", funnel.get("pulse_raw", 0))
        pulse_part = f"{pulse_label}:{p_filt}/{p_raw}"

    whl_active = oh.get("whale_active", 0)
    whl_total = oh.get("whale_total", 0)
    whl_part = f"Whl:{whl_active}/{whl_total}"

    paper_open = result.get("paper_open", 0)
    ppr_part = f"Ppr:{paper_open}"

    return f"\U0001f4e1 {nan_part} | {bird_part} | {pulse_part} | {whl_part} | {ppr_part}"


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
        f"\u2696\ufe0f S5 ARBITRATION ALERT\n\n"
        f"Token: {symbol} ({mint[:12]}...)\n"
        f"Conflict: {conflict}\n"
        f"Scores: ordering={score.ordering_score}, "
        f"permission={score.permission_score}\n"
        f"Red flags: {score.red_flags}\n\n"
        f"Grok wanted TRADE \u2192 system downgraded to WATCHLIST.\n"
        f"Override? Send manual trade command if you disagree."
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": channel_id, "text": text},
            )
    except Exception as e:
        _record_error(None, "execution", e, {"detail": "S5 Telegram alert failed", "symbol": symbol})


# Grok alpha override system prompt
GROK_ALPHA_PROMPT = """You are ChadBoar's alpha brain. DENSE YAML only.
Given signal data for a token, decide if this is alpha worth trading.
Rug Warden already PASSED \u2014 safety is cleared. Your job: pattern match.

Respond with EXACTLY this YAML format (no markdown fences):
verdict: TRADE | NOPE
reasoning: <one sentence \u2014 pattern match + conviction chain>
confidence: <0.0-1.0>

TRADE = upgrade to AUTO_EXECUTE. NOPE = stay on WATCHLIST.
Only say TRADE if you see genuine convergence (whale + narrative + volume).
Be ruthless. Most things are NOPE."""


# ── Stage Functions ─────────────────────────────────────────────────


def stage_init_context(
    bead_chain, cycle_start: datetime, cycle_num: int,
) -> None:
    """Emit POLICY + MODEL_VERSION beads on first boot or config change.
    Check for watchdog alerts. Mutates nothing except bead chain."""
    if not bead_chain:
        return

    # Check if context beads need emitting
    _should_emit = False
    try:
        existing_policy = bead_chain.query_by_type(BeadType.POLICY, limit=1)
        existing_model = bead_chain.query_by_type(BeadType.MODEL_VERSION, limit=1)
        if not existing_policy or not existing_model:
            _should_emit = True
        else:
            import hashlib as _hl
            risk_path = Path("config/risk.yaml")
            if risk_path.exists():
                current_hash = _hl.sha256(risk_path.read_bytes()).hexdigest()[:16]
                last_policy = existing_policy[0]
                last_hash = last_policy.content.get("rules", {}).get("_config_hash", "")
                if current_hash != last_hash:
                    _should_emit = True
    except Exception as e:
        _record_error(bead_chain, "bead_write", e, {"detail": "Policy/model check failed, will re-emit"}, cycle_start)
        _should_emit = True

    if _should_emit:
        try:
            import yaml
            risk_path = Path("config/risk.yaml")
            if risk_path.exists():
                risk_rules = yaml.safe_load(risk_path.read_text()) or {}
                import hashlib as _hl
                risk_rules["_config_hash"] = _hl.sha256(
                    risk_path.read_bytes()
                ).hexdigest()[:16]
                emit_policy_bead(
                    bead_chain,
                    policy_name="risk_config",
                    policy_type="RISK",
                    rules=risk_rules,
                    authority="G",
                )
        except Exception as e:
            _record_error(bead_chain, "bead_write", e, {"bead_type": "POLICY"}, cycle_start)
        try:
            emit_model_version_bead(
                bead_chain,
                model_name="grok-4-1-fast",
                version_hash="openrouter",
                purpose="heartbeat",
                config_snapshot={"provider": "openrouter", "model": "x-ai/grok-4-1-fast"},
            )
        except Exception as e:
            _record_error(bead_chain, "bead_write", e, {"bead_type": "MODEL_VERSION"}, cycle_start)

    # Check for watchdog alert (hallucination detection)
    watchdog_alert_path = Path("state/watchdog_alert.json")
    if watchdog_alert_path.exists():
        try:
            _wd_alert = json.loads(watchdog_alert_path.read_text())
            emit_claim_bead(
                bead_chain,
                conclusion=(
                    f"Watchdog detected heartbeat hallucination: canary stale for "
                    f"{_wd_alert.get('stale_since_minutes', '?')} minutes"
                ),
                reasoning_trace=(
                    f"Watchdog detected at {_wd_alert.get('detected_at', '?')}. "
                    f"Telegram alert sent: {_wd_alert.get('alert_sent', False)}"
                ),
                confidence_basis="watchdog_canary",
                domain="watchdog_alert",
                cycle_start=cycle_start,
                cycle_end=datetime.now(timezone.utc),
            )
            watchdog_alert_path.unlink(missing_ok=True)
        except Exception as e:
            _record_error(bead_chain, "state_update", e, {"detail": "Failed to process watchdog alert"}, cycle_start)


async def stage_watchdog(
    state: dict, bead_chain, result: dict, cycle_health: dict,
    time_remaining,
) -> None:
    """Run position watchdog. Mutates result['exits'] and cycle_health."""
    _wd_elapsed = _stage_timer()
    birdeye_watchdog = BirdeyeClient()
    try:
        exit_decisions = await asyncio.wait_for(
            run_position_watchdog(state, birdeye_watchdog),
            timeout=min(30, time_remaining())
        )
        result["exits"] = exit_decisions
        cycle_health["stages"]["watchdog"] = {
            "status": "ok",
            "exits_found": len(exit_decisions),
            "positions_checked": len(state.get("positions", [])),
            "duration_ms": _wd_elapsed(),
        }
    except asyncio.TimeoutError:
        result["errors"].append("Watchdog step timeout")
        result["timeout_triggered"] = True
        result["observe_only"] = True
        cycle_health["stages"]["watchdog"] = {"status": "timeout", "duration_ms": _wd_elapsed()}
    except Exception as e:
        result["errors"].append(f"Watchdog error: {e}")
        cycle_health["stages"]["watchdog"] = {"status": "failed", "error": str(e), "duration_ms": _wd_elapsed()}
    finally:
        await birdeye_watchdog.close()


async def stage_execute_exits(
    state: dict, result: dict, cycle_health: dict,
    state_path: Path, dry_run: bool, time_remaining,
) -> None:
    """Execute sell orders for exit decisions generated by the watchdog."""
    exit_decisions = result.get("exits", [])
    if not exit_decisions:
        return

    _exit_elapsed = _stage_timer()
    executed = 0
    failed = 0
    sol_returned_total = 0.0

    wallet_pubkey = ""
    if not dry_run:
        try:
            wallet_pubkey = get_wallet_pubkey()
        except Exception:
            result["errors"].append("Exit execution skipped — no wallet pubkey")
            return

    for decision in exit_decisions:
        if time_remaining() < 5:
            result["errors"].append("Timeout during exit execution")
            break

        mint = decision["token_mint"]
        symbol = decision.get("symbol", mint[:8])
        exit_pct = decision.get("exit_pct", 100)

        # Find the position in current state
        state = safe_read_json(state_path)
        pos = next(
            (p for p in state.get("positions", []) if p["token_mint"] == mint),
            None,
        )
        if not pos:
            continue

        token_amount = pos.get("entry_amount_tokens", 0)
        if token_amount <= 0:
            continue

        # Partial sell: compute amount to sell
        sell_amount = int(token_amount * exit_pct / 100)
        if sell_amount <= 0:
            continue

        entry_sol = pos.get("entry_amount_sol", 0)
        sol_portion = entry_sol * exit_pct / 100

        # Escalating slippage for stop-loss / critical exits.
        # Micro-cap tokens that trigger SL often have thin liquidity —
        # 5% slippage fails with Custom 6024.  Getting partial value
        # back beats holding to zero.
        is_critical = decision.get("urgency") in ("critical", "high")
        slippage_levels = [500, 1500, 4900] if is_critical else [500]

        sell_result = None
        for slippage_bps in slippage_levels:
            sell_result = await execute_swap(
                direction="sell",
                token_mint=mint,
                amount=sell_amount,
                dry_run=dry_run,
                slippage_bps=slippage_bps,
                wallet_pubkey=wallet_pubkey,
            )
            sell_status = sell_result.get("status", "")
            if sell_status in ("SUCCESS", "DRY_RUN"):
                break
            err_str = sell_result.get("error", "")
            # Custom 6024 = Jupiter ExceededSlippageTolerance — retry with
            # higher slippage.  Any other failure is not slippage-related.
            if "6024" not in err_str:
                break

        if sell_result.get("status") == "SUCCESS":
            sell_out = float(sell_result.get("amount_out", 0))
            sol_received = sell_out / 1e9 if sell_out > 0 else sol_portion
            executed += 1
        elif sell_result.get("status") == "DRY_RUN":
            sol_received = sol_portion  # Estimate for dry run
            executed += 1
        else:
            result["errors"].append(
                f"Exit sell FAILED for {symbol}: {sell_result.get('error', 'unknown')}"
            )
            failed += 1
            continue

        sol_returned_total += sol_received

        # Win/loss tracking (same logic as pulse_quick_scan)
        pnl_pct_exit = ((sol_received - sol_portion) / sol_portion * 100) if sol_portion > 0 else 0.0
        is_win = sol_received > sol_portion

        # Update state atomically
        state = safe_read_json(state_path)
        if exit_pct >= 100:
            # Full exit — remove THIS position only (not all entries
            # sharing the same mint, which breaks duplicate-mint positions
            # like XMN x2).
            found = False
            new_positions = []
            for p in state.get("positions", []):
                if not found and p["token_mint"] == mint:
                    found = True  # skip first match
                    continue
                new_positions.append(p)
            state["positions"] = new_positions
        else:
            # Partial exit — reduce token amount and SOL allocation
            for p in state["positions"]:
                if p["token_mint"] == mint:
                    p["entry_amount_tokens"] = token_amount - sell_amount
                    p["entry_amount_sol"] = entry_sol - sol_portion
                    break

        state["current_balance_sol"] = (
            state.get("current_balance_sol", 0) + sol_received
        )
        state["total_trades"] = state.get("total_trades", 0) + 1
        if is_win:
            state["total_wins"] = state.get("total_wins", 0) + 1
            state["consecutive_losses"] = 0
        else:
            state["total_losses"] = state.get("total_losses", 0) + 1
            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
            bal = max(state.get("current_balance_sol", 1), 0.01)
            loss_contribution = abs(pnl_pct_exit / 100) * sol_portion / bal * 100
            state["daily_loss_pct"] = state.get("daily_loss_pct", 0) + loss_contribution
        safe_write_json(state_path, state)

        result["decisions"].append(
            f"\U0001f4b0 EXIT {symbol}: {decision.get('reason', '?')} "
            f"({exit_pct}% sold, +{sol_received:.4f} SOL)"
        )

    cycle_health["stages"]["exit_execution"] = {
        "status": "ok",
        "exits_attempted": len(exit_decisions),
        "exits_executed": executed,
        "exits_failed": failed,
        "sol_returned": round(sol_returned_total, 6),
        "duration_ms": _exit_elapsed(),
    }


async def stage_oracle(
    bead_chain, result: dict, funnel: dict, cycle_start: datetime,
    cycle_health: dict, time_remaining,
) -> tuple[list, bool]:
    """Query oracle sources. Returns (oracle_signals, oracle_failed)."""
    _oracle_elapsed = _stage_timer()
    oracle_failed = False
    oracle_signals: list = []

    # Skip Nansen TGM calls when graduation config says so — saves API credits
    # and avoids rate limits (Nansen scores at 0 points for graduation plays).
    _skip_nansen = False
    try:
        import yaml as _yaml
        _risk = _yaml.safe_load(Path("config/risk.yaml").read_text()) or {}
        _skip_nansen = _risk.get("conviction", {}).get("graduation", {}).get("skip_nansen", False)
    except Exception:
        pass

    try:
        oracle_result = await asyncio.wait_for(
            query_oracle(skip_nansen=_skip_nansen),
            timeout=min(45, time_remaining())
        )
        if oracle_result.get("status") == "OK":
            oracle_signals = oracle_result.get("nansen_signals", [])
            result["oracle_signals"] = oracle_signals
            result["holdings_delta"] = oracle_result.get("holdings_delta", [])
            result["phase_timing"] = oracle_result.get("phase_timing", {})
            result["oracle_diagnostics"] = oracle_result.get("diagnostics", [])
            result["oracle_health"] = oracle_result.get("source_health", {})

            # Merge Mobula whale signals
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

            # Merge Pulse candidates
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
            oracle_failed = True
            result["sources_failed"].append("oracle")
            result["errors"].append(f"Oracle error: {oracle_result.get('error', 'unknown')}")
    except asyncio.TimeoutError:
        result["errors"].append("Oracle step timeout")
        oracle_failed = True
        result["sources_failed"].append("oracle")
    except Exception as e:
        result["errors"].append(f"Oracle error: {e}")
        oracle_failed = True
        result["sources_failed"].append("oracle")

    # Funnel counts
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

    # Record stage health
    _oracle_source_detail = (
        f"nansen:{funnel['nansen_filtered']}/{funnel['nansen_raw']}, "
        f"mobula:{funnel['mobula_resolved']}/{funnel['mobula_raw']}, "
        f"pulse:{funnel['pulse_filtered']}/{funnel['pulse_raw']}"
    ) if not oracle_failed else "all sources failed"
    cycle_health["stages"]["oracle"] = {
        "status": "failed" if oracle_failed else "ok",
        "detail": _oracle_source_detail,
        "candidates_found": len(oracle_signals),
        "duration_ms": _oracle_elapsed(),
    }

    # FACT bead
    if bead_chain:
        _oracle_status = "ERR" if oracle_failed else "OK"
        _fid = emit_fact_bead(
            bead_chain, provider="oracle",
            field="whale_scan_summary",
            value={
                "nansen_raw": funnel["nansen_raw"],
                "nansen_filtered": funnel["nansen_filtered"],
                "mobula_raw": funnel["mobula_raw"],
                "pulse_raw": funnel["pulse_raw"],
                "total_candidates": len(oracle_signals),
                "status": _oracle_status,
            },
            cycle_start=cycle_start,
            cycle_end=datetime.now(timezone.utc),
            source_status=_oracle_status,
        )
        if _fid:
            result.setdefault("_fact_bead_ids", []).append(_fid)

    return oracle_signals, oracle_failed


async def stage_narrative(
    bead_chain, result: dict, funnel: dict, cycle_start: datetime,
    cycle_health: dict,
) -> tuple[list, bool, NarrativeTracker]:
    """Query narrative sources. Returns (narrative_signals, narrative_failed, tracker)."""
    _nar_elapsed = _stage_timer()
    narrative_failed = False
    narrative_tracker = NarrativeTracker()
    birdeye_status = "SKIP"
    dexscreener_status = "OK"
    narrative_signals: list = []

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

        # Fallback: Birdeye
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
            except Exception as e2:
                _record_error(bead_chain, "narrative_hunter", e2, {"detail": "Failed to read Birdeye error response"}, cycle_start)
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

    # Funnel counts
    funnel["narrative_raw"] = len(narrative_signals)
    funnel["narrative_with_spike"] = len([
        s for s in narrative_signals
        if float(s.get("volume_vs_avg", "0x").replace("x", "")) >= 2.0
    ])

    # Record stage health
    _nar_detail = f"dexscreener:{dexscreener_status}, birdeye:{birdeye_status}"
    cycle_health["stages"]["narrative"] = {
        "status": "failed" if narrative_failed else ("partial" if dexscreener_status != "OK" else "ok"),
        "detail": _nar_detail,
        "signals_found": funnel["narrative_raw"],
        "volume_spikes": funnel["narrative_with_spike"],
        "duration_ms": _nar_elapsed(),
    }

    # FACT bead
    if bead_chain:
        _nar_status = "ERR" if narrative_failed else "OK"
        _fid = emit_fact_bead(
            bead_chain, provider="dexscreener",
            field="narrative_summary",
            value={
                "candidates_raw": funnel["narrative_raw"],
                "volume_spikes": funnel["narrative_with_spike"],
                "dexscreener_status": dexscreener_status,
                "birdeye_status": birdeye_status,
            },
            cycle_start=cycle_start,
            cycle_end=datetime.now(timezone.utc),
            source_status=_nar_status,
        )
        if _fid:
            result.setdefault("_fact_bead_ids", []).append(_fid)

    return narrative_signals, narrative_failed, narrative_tracker


async def stage_score_and_execute(
    oracle_signals: list, narrative_signals: list,
    narrative_tracker: NarrativeTracker,
    oracle_failed: bool, narrative_failed: bool,
    state: dict, bead_chain, result: dict, funnel: dict,
    cycle_start: datetime, cycle_health: dict,
    state_path: Path, dry_run: bool,
) -> int:
    """Score candidates, apply warden, emit beads, execute trades.

    Returns proposal_count. Mutates result, funnel, state (on live trades).
    """
    _scoring_elapsed = _stage_timer()

    # Data completeness penalty
    sources_failed_count = len(result["sources_failed"])
    if sources_failed_count >= 2:
        result["observe_only"] = True
        result["data_completeness"] = 0.0
        result["decisions"].append("OBSERVE-ONLY MODE: \u22652 primary sources failed (oracle, narrative)")
        try:
            result["paper_open"] = len([t for t in _load_paper_trades() if not t.get("closed")])
        except Exception as e:
            _record_error(bead_chain, "paper_trade", e, {"detail": "Failed to load paper trades for observe-only"}, cycle_start)
            result["paper_open"] = 0
        result["health_line"] = build_health_line(result)
        cycle_health["stages"]["scoring"] = {"status": "skipped:observe_only", "duration_ms": _scoring_elapsed()}
        return 0
    elif oracle_failed:
        result["data_completeness"] = 0.7
    elif narrative_failed:
        result["data_completeness"] = 0.8
    else:
        result["data_completeness"] = 1.0

    # Pipeline regime CLAIM bead
    fact_bead_ids = result.get("_fact_bead_ids", [])
    claim_bead_ids: list[str] = []
    if bead_chain and fact_bead_ids:
        try:
            _total_candidates = len(oracle_signals) + funnel["narrative_with_spike"]
            _oracle_status = "ERR" if oracle_failed else "OK"
            _nar_status_claim = "ERR" if narrative_failed else "OK"
            _regime = "degraded" if result["data_completeness"] < 1.0 else "normal"
            if _total_candidates == 0:
                _regime = "dry"

            _claim_id = emit_claim_bead(
                bead_chain,
                conclusion=(
                    f"Pipeline {_regime}: {_total_candidates} candidates, "
                    f"data_completeness={result['data_completeness']}, "
                    f"oracle={_oracle_status}, narrative={_nar_status_claim}"
                ),
                reasoning_trace=(
                    f"oracle_signals={len(oracle_signals)}, "
                    f"narrative_spikes={funnel['narrative_with_spike']}, "
                    f"sources_failed={result['sources_failed']}"
                ),
                confidence_basis="source_health",
                domain="pipeline_regime",
                premises_ref=fact_bead_ids,
                cycle_start=cycle_start,
                cycle_end=datetime.now(timezone.utc),
            )
            if _claim_id:
                claim_bead_ids.append(_claim_id)
        except Exception as e:
            _record_error(bead_chain, "bead_write", e, {"bead_type": "CLAIM", "domain": "pipeline_regime"}, cycle_start)

    # Scoring setup
    scorer = ConvictionScorer()
    proposal_count = 0

    edge_bank_bead_count = 0
    try:
        from lib.chain.bead_chain import get_chain_stats
        chain_stats = get_chain_stats()
        edge_bank_bead_count = chain_stats.get("total_beads", 0)
    except Exception as e:
        _record_error(bead_chain, "data_fetch", e, {"detail": "Edge bank chain stats unavailable"}, cycle_start)

    sol_price_usd = float(state.get("sol_price_usd", 78.0))
    daily_graduation_count = int(state.get("daily_graduation_count", 0))

    # Merge signals by token mint
    all_mints = set()
    for sig in oracle_signals:
        all_mints.add(sig["token_mint"])
    for sig in narrative_signals:
        all_mints.add(sig["token_mint"])

    birdeye_red_flags = BirdeyeClient()
    funnel["reached_scorer"] = len(all_mints)

    for mint in all_mints:
        oracle_sig = next((s for s in oracle_signals if s["token_mint"] == mint), None)
        narrative_sig = next((s for s in narrative_signals if s["token_mint"] == mint), None)

        # MINIMUM VOLUME GATE: Skip tokens with <$5k volume (39% of trades were
        # on dead/illiquid tokens with 5% win rate — pure noise in the bead stream)
        _vol_usd = float((oracle_sig or {}).get("volume_usd",
                         (oracle_sig or {}).get("total_buy_usd", 0)))
        if _vol_usd < 5000:
            funnel["scored_discard"] = funnel.get("scored_discard", 0) + 1
            result["decisions"].append(f"\U0001f417 SKIP: {mint[:8]} — volume ${_vol_usd:,.0f} < $5k minimum")
            continue

        if oracle_sig and oracle_sig.get("buyer_depth", {}).get("smart_money_buyers", 0) > 0:
            whales = oracle_sig["buyer_depth"]["smart_money_buyers"]
        else:
            whales = oracle_sig["wallet_count"] if oracle_sig else 0

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

        rug_status = await run_rug_warden(mint, play_type=pre_play_type, pre_liquidity_usd=pre_liquidity or None)

        concentrated_vol = False
        dumper_count = 0
        try:
            trades_data = await birdeye_red_flags.get_trades(mint, limit=100)
            concentrated_vol, vol_reason = check_concentrated_volume(trades_data)
        except Exception as e:
            result["errors"].append(f"Volume concentration check failed for {mint[:8]}: {e}")

        time_mismatch_detected = (
            whales >= 3 and volume_spike >= 5.0 and age_minutes < 5
        )

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

        holder_delta = 0.0
        try:
            holder_data = await birdeye_red_flags.get_holder_count(mint)
            h_data = holder_data.get("data", holder_data)
            if isinstance(h_data, dict):
                holder_delta = float(h_data.get("holder_change_24h_percent",
                                     h_data.get("holderChangePercent", 0)))
        except Exception as e:
            result["errors"].append(f"Holder delta fetch failed for {mint[:8]}: {e}")

        signal_input = SignalInput(
            smart_money_whales=whales,
            narrative_volume_spike=volume_spike,
            narrative_kol_detected=kol_detected,
            narrative_age_minutes=age_minutes,
            rug_warden_status=rug_status,
            edge_bank_match_pct=0.0,
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

        # Grok alpha override
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
                    if "verdict: TRADE" in grok_content or "verdict:TRADE" in grok_content:
                        score.recommendation = "AUTO_EXECUTE"
                        score.reasoning += f" | GROK OVERRIDE: {grok_content}"
                    else:
                        score.reasoning += f" | GROK: NOPE \u2014 staying WATCHLIST"
                else:
                    result["errors"].append(f"Grok override failed: {grok_result.get('error', 'unknown')}")
            except Exception as e:
                result["errors"].append(f"Grok override error for {mint[:8]}: {e}")

        # S5 Arbitration
        token_symbol = (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN")
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
                result["decisions"].append(f"\u2696\ufe0f S5 CONFLICT: {s5_conflict}")
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

        # Emit SIGNAL bead
        signal_bead_id = ""
        if bead_chain:
            _wv = rug_status if rug_status in ("PASS", "WARN", "FAIL") else "UNKNOWN"
            _disc = (oracle_sig or {}).get("discovery_source", "unknown")
            signal_bead_id = emit_signal_bead(
                bead_chain,
                token_mint=mint,
                token_symbol=opportunity.get("token_symbol", "UNKNOWN"),
                play_type=score.play_type,
                discovery_source=_disc,
                scoring_breakdown=score.breakdown,
                conviction_score=score.permission_score,
                warden_verdict=_wv,
                red_flags=score.red_flags if isinstance(score.red_flags, dict) else {"flags": score.red_flags},
                raw_metrics=opportunity.get("enrichment", {}),
                fact_bead_ids=fact_bead_ids,
                claim_bead_ids=claim_bead_ids,
            )
        opportunity["signal_bead_id"] = signal_bead_id
        opportunity["verdict_bead_id"] = signal_bead_id

        result["opportunities"].append(opportunity)

        # Funnel counts
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
            if bead_chain and signal_bead_id:
                _wd = {"rug_status": rug_status, "reasoning": score.reasoning}
                emit_proposal_rejected_bead(
                    bead_chain, signal_bead_id=signal_bead_id,
                    token_mint=mint, token_symbol=opportunity.get("token_symbol", "UNKNOWN"),
                    rejection_source="rug_warden",
                    rejection_reason=score.reasoning,
                    rejection_category=RejectionCategory.WARDEN_VETO,
                    scoring_breakdown=score.breakdown,
                    warden_detail=_wd,
                    risk_metrics={"pot_sol": state.get("current_balance_sol", 0)},
                )
            result["decisions"].append(f"\U0001f417 VETO: {mint[:8]} \u2014 {score.reasoning}")
        elif score.recommendation == "DISCARD":
            if bead_chain and signal_bead_id:
                emit_proposal_rejected_bead(
                    bead_chain, signal_bead_id=signal_bead_id,
                    token_mint=mint, token_symbol=opportunity.get("token_symbol", "UNKNOWN"),
                    rejection_source="scoring",
                    rejection_reason=f"permission {score.permission_score} < {scorer.thresholds.get('paper_trade', 30)}",
                    rejection_category=RejectionCategory.SCORE_BELOW_THRESHOLD,
                    scoring_breakdown=score.breakdown,
                    risk_metrics={"pot_sol": state.get("current_balance_sol", 0)},
                )
            result["decisions"].append(f"\U0001f417 NOPE: {mint[:8]} \u2014 permission {score.permission_score} < {scorer.thresholds.get('paper_trade', 30)}")
        elif score.recommendation == "PAPER_TRADE":
            token_symbol = (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN")
            try:
                _entry_fdv = market_cap
                if _entry_fdv == 0:
                    try:
                        _ov = await birdeye_red_flags.get_token_overview(mint)
                        _ov_data = _ov.get("data", _ov)
                        _entry_fdv = float(_ov_data.get("mc", _ov_data.get("fdv", 0)))
                    except Exception:
                        pass
                paper_candidate = {
                    "token_mint": mint, "token_symbol": token_symbol,
                    "price_usd": _entry_fdv,
                    "liquidity_usd": float((oracle_sig or {}).get("liquidity_usd", 0)),
                    "volume_usd": float((oracle_sig or {}).get("volume_usd", (oracle_sig or {}).get("total_buy_usd", 0))),
                    "source": (oracle_sig or {}).get("source", "unknown"),
                    "discovery_source": (oracle_sig or {}).get("discovery_source", "unknown"),
                    "score": {
                        "play_type": score.play_type, "permission_score": score.permission_score,
                        "ordering_score": score.ordering_score, "recommendation": score.recommendation,
                        "breakdown": score.breakdown, "red_flags": score.red_flags,
                    },
                    "warden": {"verdict": rug_status},
                    "verdict_bead_id": signal_bead_id,
                }
                trade_record = log_paper_trade(paper_candidate)
                if rug_status in ("PASS", "WARN"):
                    try:
                        write_paper_bead(trade_record)
                    except Exception as e:
                        _record_error(bead_chain, "paper_trade", e, {"token_mint": mint, "detail": "write_paper_bead failed"}, cycle_start)
                proposal_bead_id = ""
                if bead_chain and signal_bead_id:
                    proposal_bead_id = emit_proposal_bead(
                        bead_chain, signal_bead_id=signal_bead_id,
                        action="ENTER_LONG", token_mint=mint, token_symbol=token_symbol,
                        entry_price_fdv=trade_record.get("entry_price_fdv"),
                        position_size_sol=score.position_size_sol,
                        execution_venue="paper", gate="auto",
                    )
                    if proposal_bead_id:
                        proposal_count += 1
                    update_trade_bead_id(trade_record["id"], proposal_bead_id, signal_bead_id)
                result["decisions"].append(
                    f"\U0001f417\U0001f4dd PAPER: {token_symbol} ({mint[:8]}) \u2014 [{score.play_type}] "
                    f"permission {score.permission_score}, ordering {score.ordering_score}"
                )
            except Exception as e:
                result["errors"].append(f"Paper trade logging failed for {mint[:8]}: {e}")
                result["decisions"].append(
                    f"\U0001f417\U0001f4dd PAPER (log failed): {mint[:8]} \u2014 [{score.play_type}] "
                    f"permission {score.permission_score}"
                )
        elif score.recommendation == "WATCHLIST":
            token_symbol = (oracle_sig or narrative_sig or {}).get("token_symbol", "UNKNOWN")
            try:
                _entry_fdv = market_cap
                if _entry_fdv == 0:
                    try:
                        _ov = await birdeye_red_flags.get_token_overview(mint)
                        _ov_data = _ov.get("data", _ov)
                        _entry_fdv = float(_ov_data.get("mc", _ov_data.get("fdv", 0)))
                    except Exception:
                        pass
                paper_candidate = {
                    "token_mint": mint, "token_symbol": token_symbol,
                    "price_usd": _entry_fdv,
                    "liquidity_usd": float((oracle_sig or {}).get("liquidity_usd", 0)),
                    "volume_usd": float((oracle_sig or {}).get("volume_usd", (oracle_sig or {}).get("total_buy_usd", 0))),
                    "source": (oracle_sig or {}).get("source", "unknown"),
                    "discovery_source": (oracle_sig or {}).get("discovery_source", "unknown"),
                    "score": {
                        "play_type": score.play_type, "permission_score": score.permission_score,
                        "ordering_score": score.ordering_score, "recommendation": score.recommendation,
                        "breakdown": score.breakdown, "red_flags": score.red_flags,
                    },
                    "warden": {"verdict": rug_status},
                    "verdict_bead_id": signal_bead_id,
                }
                trade_record = log_paper_trade(paper_candidate)
                if rug_status in ("PASS", "WARN"):
                    try:
                        write_paper_bead(trade_record)
                    except Exception as e:
                        _record_error(bead_chain, "paper_trade", e, {"token_mint": mint, "detail": "write_paper_bead failed (watchlist)"}, cycle_start)
                if bead_chain and signal_bead_id:
                    _prop_id = emit_proposal_bead(
                        bead_chain, signal_bead_id=signal_bead_id,
                        action="ENTER_LONG", token_mint=mint, token_symbol=token_symbol,
                        entry_price_fdv=trade_record.get("entry_price_fdv"),
                        position_size_sol=score.position_size_sol,
                        execution_venue="paper", gate="auto",
                    )
                    if _prop_id:
                        proposal_count += 1
                    update_trade_bead_id(trade_record["id"], _prop_id, signal_bead_id)
            except Exception as e:
                _record_error(bead_chain, "paper_trade", e, {"token_mint": mint, "recommendation": "WATCHLIST"}, cycle_start)
            result["decisions"].append(
                f"\U0001f417 WATCHLIST+PAPER: {token_symbol} ({mint[:8]}) \u2014 [{score.play_type}] "
                f"permission {score.permission_score}, ordering {score.ordering_score}, "
                f"primary {len(score.primary_sources)}"
            )
        elif score.recommendation == "AUTO_EXECUTE":
            # Check per-mint position limit (max 2 entries per token)
            state_fresh = safe_read_json(state_path)
            mint_count = sum(1 for p in state_fresh.get("positions", []) if p["token_mint"] == mint)
            if mint_count >= 2:
                result["decisions"].append(f"\U0001f417 SKIP: {mint[:8]} — already {mint_count} entries (max 2)")
                continue

            if score.play_type == "graduation":
                daily_graduation_count += 1

            if dry_run:
                result["decisions"].append(
                    f"\U0001f417\U0001f525 DRY-RUN TRADE: {mint[:8]} \u2014 [{score.play_type}] would YOLO {score.position_size_sol:.4f} SOL "
                    f"(permission {score.permission_score}, ordering {score.ordering_score}, "
                    f"primary {len(score.primary_sources)}) OINK!"
                )
                # Emit proposal bead for dry-run so signal chain is complete
                if bead_chain and signal_bead_id:
                    emit_proposal_bead(
                        bead_chain, signal_bead_id=signal_bead_id,
                        action="ENTER_LONG", token_mint=mint,
                        token_symbol=token_symbol,
                        position_size_sol=score.position_size_sol,
                        execution_venue="paper", gate="dry_run",
                    )
            else:
                result["decisions"].append(
                    f"\U0001f417\U0001f525 EXECUTE: {mint[:8]} \u2014 [{score.play_type}] {score.position_size_sol:.4f} SOL "
                    f"(permission {score.permission_score}, ordering {score.ordering_score}) OINK!"
                )
                try:
                    wallet_pubkey = get_wallet_pubkey()
                    slippage_bps = 500 if score.play_type == "graduation" else 300

                    buy_result = await execute_swap(
                        direction="buy",
                        token_mint=mint,
                        amount=score.position_size_sol,
                        dry_run=False,
                        slippage_bps=slippage_bps,
                        wallet_pubkey=wallet_pubkey,
                    )

                    buy_status = buy_result.get("status")
                    if buy_status != "SUCCESS":
                        error_msg = buy_result.get("error", "unknown")
                        result["errors"].append(f"Trade FAILED for {mint[:8]}: {error_msg}")
                        # Record failed execution in bead chain
                        if bead_chain and signal_bead_id:
                            emit_proposal_rejected_bead(
                                bead_chain, signal_bead_id=signal_bead_id,
                                token_mint=mint, token_symbol=token_symbol,
                                rejection_source="execution",
                                rejection_reason=f"Swap failed: {error_msg}",
                                rejection_category=RejectionCategory.SCORE_BELOW_THRESHOLD,
                                scoring_breakdown=score.breakdown,
                                risk_metrics={"pot_sol": state.get("current_balance_sol", 0)},
                            )
                    else:
                        amount_out = float(buy_result.get("amount_out", 0))
                        entry_price = 0.0
                        if amount_out > 0:
                            amount_in_sol = float(buy_result.get("amount_in", 0)) / 1e9
                            if amount_in_sol > 0:
                                entry_price = (amount_in_sol * sol_price_usd) / amount_out

                        tx_sig = buy_result.get("tx_signature", "")
                        now = datetime.utcnow().isoformat()
                        new_position = {
                            "token_mint": mint,
                            "token_symbol": token_symbol,
                            "direction": "long",
                            "entry_price": entry_price,
                            "entry_amount_sol": score.position_size_sol,
                            "entry_amount_tokens": amount_out,
                            "entry_time": now,
                            "peak_price": entry_price,
                            "entry_market_cap_usd": market_cap,
                            "play_type": score.play_type,
                            "tx_signature": tx_sig,
                            "thesis": (
                                f"{score.play_type}: perm {score.permission_score}, "
                                f"ord {score.ordering_score}, "
                                f"primary {len(score.primary_sources)}"
                            ),
                            "signals": score.primary_sources,
                        }

                        # Atomic state update (re-read for freshness)
                        state = safe_read_json(state_path)
                        state.setdefault("positions", []).append(new_position)
                        state["daily_exposure_sol"] = (
                            state.get("daily_exposure_sol", 0) + score.position_size_sol
                        )
                        state["current_balance_sol"] = (
                            state.get("current_balance_sol", 0) - score.position_size_sol
                        )
                        state["total_trades"] = state.get("total_trades", 0) + 1
                        state["last_trade_time"] = now
                        safe_write_json(state_path, state)

                        result["decisions"].append(
                            f"  -> BUY OK: {amount_out:.2f} tokens, entry ${entry_price:.6f}, tx={tx_sig[:16]}..." if tx_sig else
                            f"  -> BUY OK: {amount_out:.2f} tokens, entry ${entry_price:.6f}, tx=NONE"
                        )

                        # Emit PROPOSAL bead for live trade
                        if bead_chain and signal_bead_id:
                            emit_proposal_bead(
                                bead_chain, signal_bead_id=signal_bead_id,
                                action="ENTER_LONG", token_mint=mint,
                                token_symbol=token_symbol,
                                position_size_sol=score.position_size_sol,
                                execution_venue="jupiter_jito", gate="auto",
                                tx_signature=tx_sig,
                            )

                except Exception as e:
                    result["errors"].append(f"Trade execution error for {mint[:8]}: {e}")

    await birdeye_red_flags.close()

    # Record scoring stage health
    _highest_score = max((o.get("permission_score", 0) for o in result["opportunities"]), default=0)
    cycle_health["stages"]["scoring"] = {
        "status": "ok",
        "candidates_scored": funnel["reached_scorer"],
        "above_threshold": funnel["scored_execute"] + funnel["scored_watchlist"] + funnel["scored_paper_trade"],
        "highest_score": _highest_score,
        "duration_ms": _scoring_elapsed(),
    }
    cycle_health["stages"]["warden"] = {
        "status": "ok",
        "checked": funnel["reached_scorer"],
        "passed": funnel["reached_scorer"] - funnel["scored_veto"],
        "vetoed": funnel["scored_veto"],
    }
    cycle_health["stages"]["execution"] = {
        "status": "ok" if funnel["scored_execute"] > 0 else "skipped:no_qualifying",
        "trades_attempted": funnel["scored_execute"],
        "proposals_emitted": proposal_count,
    }

    return proposal_count


async def stage_finalize(
    state: dict, bead_chain, result: dict, funnel: dict,
    cycle_start: datetime, cycle_num: int, dry_run: bool,
    cycle_health: dict, start_time: float, state_path: Path,
    proposal_count: int,
) -> None:
    """Paper PnL, state write, heartbeat bead, canary. Mutates result."""
    # Paper PnL check
    try:
        pnl_result = await check_paper_trades(bead_chain=bead_chain)
        result["paper_pnl_checked"] = pnl_result.get("checked", 0)
    except Exception as e:
        result["errors"].append(f"Paper PnL check failed: {e}")
        result["paper_pnl_checked"] = 0

    # Re-read state from file to pick up any position changes written by
    # stage_score_and_execute (which writes positions atomically to disk).
    # Without this re-read, the stale `state` dict from run_heartbeat()
    # would overwrite live position entries.
    state = safe_read_json(state_path)
    if dry_run:
        state["dry_run_cycles_completed"] = cycle_num
    state["last_heartbeat_time"] = datetime.utcnow().isoformat()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("daily_date") != today:
        state["daily_graduation_count"] = 0

    safe_write_json(state_path, state)

    # ── Auto-generate state/latest.md ──────────────────────────────────
    # Deterministic snapshot so Grok (and humans) always see accurate
    # numbers.  Previously Grok wrote this file itself and hallucinated
    # balances and position counts.
    try:
        positions = state.get("positions", [])
        # Group positions by mint
        by_mint: dict[str, list[dict]] = {}
        for p in positions:
            by_mint.setdefault(p["token_mint"], []).append(p)

        pos_lines = []
        for mint, entries in by_mint.items():
            sym = entries[0].get("token_symbol", mint[:8])
            total_tokens = sum(e.get("entry_amount_tokens", 0) for e in entries)
            total_sol = sum(e.get("entry_amount_sol", 0) for e in entries)
            avg_mc = sum(e.get("entry_market_cap_usd", 0) for e in entries) / len(entries)
            pos_lines.append(
                f"  {sym} x{len(entries)} | {total_sol:.4f} SOL | "
                f"avg mc ${avg_mc:,.0f} | {total_tokens/1e6:.1f}M tokens"
            )

        unique_tokens = len(by_mint)
        total_deployed = sum(p.get("entry_amount_sol", 0) for p in positions)
        bal = state.get("current_balance_sol", 0)
        wins = state.get("total_wins", 0)
        losses = state.get("total_losses", 0)
        consec = state.get("consecutive_losses", 0)
        paper_open = result.get("paper_open", 0)
        hb_time = state.get("last_heartbeat_time", "unknown")
        health = result.get("health_line", "DIAG UNAVAILABLE")

        decisions_summary = "; ".join(result.get("decisions", [])[:8]) or "none"
        errors_summary = "; ".join(result.get("errors", [])[:5]) or "none"

        latest_md = (
            f"# ChadBoar Status — {today}\n\n"
            f"**Balance:** {bal:.4f} SOL\n"
            f"**Deployed:** {total_deployed:.4f} SOL ({total_deployed/max(bal+total_deployed,0.01)*100:.1f}% of pot)\n"
            f"**Positions:** {len(positions)} entries across {unique_tokens} tokens\n"
            f"**W/L:** {wins}W {losses}L (consec losses: {consec})\n"
            f"**Paper Open:** {paper_open}\n"
            f"**Mode:** {'DRY RUN' if dry_run else 'LIVE'}\n"
            f"**Last Heartbeat:** {hb_time}\n\n"
            f"## Positions\n"
        )
        if pos_lines:
            latest_md += "\n".join(pos_lines) + "\n"
        else:
            latest_md += "  (none)\n"
        latest_md += (
            f"\n## Recent Decisions\n{decisions_summary}\n"
            f"\n## Errors\n{errors_summary}\n"
            f"\n## Health\n{health}\n"
        )
        Path("state/latest.md").write_text(latest_md)
    except Exception:
        pass  # Non-critical — don't break heartbeat if latest.md write fails

    # Legacy flight recorder
    try:
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
    except Exception as e:
        _record_error(bead_chain, "bead_write", e, {"bead_type": "legacy_flight_recorder"}, cycle_start)

    # Structured heartbeat bead (v0.2)
    cycle_end = datetime.now(timezone.utc)
    cycle_health["cycle_end"] = cycle_end.isoformat()
    cycle_health["total_duration_ms"] = int((time.time() - start_time) * 1000)
    cycle_health["errors"] = result.get("errors", [])

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

            emit_heartbeat_bead(
                bead_chain,
                cycle_number=cycle_num,
                signals_found=funnel.get("reached_scorer", 0),
                signals_vetoed=funnel.get("scored_veto", 0),
                proposals_emitted=proposal_count,
                pot_sol=state.get("current_balance_sol", 0),
                positions_count=len(state.get("positions", [])),
                pipeline_health=_source_health,
                canary_hash=hashlib.sha256(
                    json.dumps(state, sort_keys=True).encode()
                ).hexdigest()[:12],
                stage_results=cycle_health["stages"],
                cycle_start=cycle_start,
                cycle_end=cycle_end,
            )
        except Exception as e:
            _record_error(bead_chain, "bead_write", e, {"bead_type": "HEARTBEAT"}, cycle_start)

    # Merkle anchor
    if bead_chain:
        try:
            trigger = bead_chain.check_anchor_trigger()
            if trigger:
                bead_chain.create_merkle_batch(trigger)
        except Exception as e:
            _record_error(bead_chain, "bead_write", e, {"detail": "Merkle anchor check failed"}, cycle_start)

    result["state_updated"] = True
    result["next_cycle"] = cycle_num + 1

    # Execution canary
    canary_path = Path("state/last_real_hb.txt")
    try:
        import hashlib as _hl
        _canary_hash = _hl.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:12]
        canary_path.write_text(
            f"{datetime.utcnow().isoformat()}|cycle={cycle_num + 1}|hash={_canary_hash}\n"
        )
    except Exception as e:
        _record_error(bead_chain, "state_update", e, {"detail": "Canary write failed"}, cycle_start)

    try:
        result["paper_open"] = len([t for t in _load_paper_trades() if not t.get("closed")])
    except Exception as e:
        _record_error(bead_chain, "paper_trade", e, {"detail": "Failed to count open paper trades"}, cycle_start)
        result["paper_open"] = 0
    result["health_line"] = build_health_line(result)


# ── Main Orchestrator ────────────────────────────────────────────────


async def run_heartbeat(timeout_seconds: float = 120.0) -> dict[str, Any]:
    """Execute full heartbeat cycle with time budget.

    Orchestrates discrete stages: init -> watchdog -> oracle -> narrative ->
    score_and_execute -> finalize. Each stage records health in cycle_health.
    """
    start_time = time.time()

    def time_remaining() -> float:
        return timeout_seconds - (time.time() - start_time)

    # Load state
    state_path = Path("state/state.json")
    state = safe_read_json(state_path)
    dry_run = state.get("dry_run_mode", True)
    cycle_num = state.get("dry_run_cycles_completed", 0) + 1

    # Init bead chain
    bead_chain = None
    if _BEADS_AVAILABLE:
        try:
            bead_chain = BeadChain()
        except Exception as e:
            _record_error(None, "bead_init", e, {"detail": "BeadChain() constructor failed"})

    cycle_start = datetime.now(timezone.utc)
    cycle_health: dict[str, Any] = {
        "cycle_number": cycle_num,
        "cycle_start": cycle_start.isoformat(),
        "stages": {},
        "errors": [],
    }

    funnel = {
        "nansen_raw": 0, "nansen_filtered": 0,
        "mobula_raw": 0, "mobula_resolved": 0,
        "pulse_raw": 0, "pulse_filtered": 0,
        "narrative_raw": 0, "narrative_with_spike": 0,
        "reached_scorer": 0, "scored_discard": 0,
        "scored_paper_trade": 0, "scored_watchlist": 0,
        "scored_execute": 0, "scored_veto": 0,
    }

    result: dict[str, Any] = {
        "cycle": cycle_num, "timestamp": datetime.utcnow().isoformat(),
        "dry_run": dry_run, "opportunities": [], "decisions": [],
        "errors": [], "exits": [], "timeout_triggered": False,
        "observe_only": False, "data_completeness": 1.0,
        "sources_failed": [], "funnel": funnel,
        "_fact_bead_ids": [],
    }

    if time_remaining() < 10:
        result["timeout_triggered"] = True
        result["observe_only"] = True
        result["errors"].append(f"Time budget exhausted before start: {time_remaining():.1f}s remaining")
        return result

    # Stage 0: Context beads + watchdog alerts
    stage_init_context(bead_chain, cycle_start, cycle_num)

    # Stage 1: Chain verification
    try:
        from lib.chain.verify import verify_on_boot, send_tamper_alert
        chain_status = verify_on_boot()
        result["chain_status"] = chain_status["status"]
        if chain_status["status"] == "TAMPERED":
            await send_tamper_alert(chain_status["details"])
            result["errors"].append(f"CHAIN TAMPERED: {chain_status['details']}")
    except Exception as e:
        result["errors"].append(f"Chain verification error: {e}")

    # Stage 2: Position watchdog
    if time_remaining() < 10:
        result["timeout_triggered"] = True
        result["observe_only"] = True
        result["errors"].append("Timeout before watchdog step")
        return result
    await stage_watchdog(state, bead_chain, result, cycle_health, time_remaining)

    # Stage 2b: Execute exits from watchdog decisions
    if result.get("exits") and not result.get("observe_only"):
        await stage_execute_exits(
            state, result, cycle_health, state_path, dry_run, time_remaining,
        )

    # Stage 3: Oracle
    if time_remaining() < 10:
        result["timeout_triggered"] = True
        result["observe_only"] = True
        result["errors"].append("Timeout before oracle step")
        return result
    oracle_signals, oracle_failed = await stage_oracle(
        bead_chain, result, funnel, cycle_start, cycle_health, time_remaining,
    )

    # Stage 4: Narrative
    narrative_signals, narrative_failed, narrative_tracker = await stage_narrative(
        bead_chain, result, funnel, cycle_start, cycle_health,
    )

    # Stage 5: Score, warden, execute
    proposal_count = await stage_score_and_execute(
        oracle_signals, narrative_signals, narrative_tracker,
        oracle_failed, narrative_failed,
        state, bead_chain, result, funnel,
        cycle_start, cycle_health, state_path, dry_run,
    )

    # Early return if observe-only was triggered during scoring
    if result.get("observe_only"):
        return result

    # Stage 6: Finalize
    await stage_finalize(
        state, bead_chain, result, funnel,
        cycle_start, cycle_num, dry_run,
        cycle_health, start_time, state_path, proposal_count,
    )

    return result


# ── Helper Functions ─────────────────────────────────────────────────


def _get_mcap_exit_tier(entry_market_cap: float, play_type: str = "accumulation") -> dict:
    """Get market-cap-aware exit parameters."""
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

    if play_type == "graduation":
        tier["decay_min"] = max(15, tier["decay_min"] // 2)

    return tier


async def run_position_watchdog(
    state: dict[str, Any],
    birdeye: BirdeyeClient,
) -> list[dict[str, Any]]:
    """Monitor open positions and generate exit decisions."""
    exit_decisions = []
    positions = state.get("positions", [])

    if not positions:
        return exit_decisions

    mints = [pos["token_mint"] for pos in positions]
    price_data = await batch_price_fetch(birdeye, mints, max_concurrent=3)

    for pos in positions:
        mint = pos["token_mint"]
        entry_price = pos["entry_price"]
        entry_sol = pos["entry_amount_sol"]
        peak_price = pos.get("peak_price", entry_price)
        entry_time = datetime.fromisoformat(pos["entry_time"])

        overview = price_data.get(mint, {})
        data = overview.get("data", overview)

        if not data:
            exit_decisions.append({
                "token_mint": mint, "symbol": pos["token_symbol"],
                "reason": "Price fetch failed", "exit_pct": 100, "urgency": "high",
            })
            continue

        current_price = float(data.get("price", 0))
        current_mc = float(data.get("mc", data.get("fdv", 0)))
        liquidity = float(data.get("liquidity", 0))

        if current_price > peak_price:
            pos["peak_price"] = current_price
            peak_price = current_price

        # Use market cap for PnL to avoid per-token unit mismatch
        # (entry_price is USD/smallest-unit from Jupiter, Birdeye price is USD/whole-token)
        entry_mc = float(pos.get("entry_market_cap_usd", 0))
        if entry_mc > 0 and current_mc > 0:
            pnl_pct = ((current_mc - entry_mc) / entry_mc) * 100
        elif current_price > 0 and entry_price > 0:
            pnl_pct = 0.0  # Skip — unit mismatch makes price-based PnL unreliable
        else:
            pnl_pct = 0.0
        peak_drawdown_pct = ((current_price - peak_price) / peak_price) * 100 if peak_price > 0 else 0.0
        age_minutes = (datetime.utcnow() - entry_time).total_seconds() / 60

        entry_mc = float(pos.get("entry_market_cap_usd", 0))
        pos_play_type = pos.get("play_type", "accumulation")
        tier = _get_mcap_exit_tier(entry_mc, pos_play_type)

        if pnl_pct <= tier["stop_loss"]:
            exit_decisions.append({
                "token_mint": mint, "symbol": pos["token_symbol"],
                "reason": f"Stop-loss hit: {pnl_pct:.1f}% (tier={tier['label']}, sl={tier['stop_loss']}%)",
                "exit_pct": 100, "urgency": "critical",
            })
        elif pnl_pct >= tier["tp1_pnl"] and not pos.get("tier1_exited", False):
            exit_decisions.append({
                "token_mint": mint, "symbol": pos["token_symbol"],
                "reason": f"TP tier 1: {pnl_pct:.1f}% (tier={tier['label']}, target={tier['tp1_pnl']}%)",
                "exit_pct": tier["tp1_sell"], "urgency": "normal",
            })
            pos["tier1_exited"] = True
        elif pnl_pct >= tier["tp2_pnl"] and not pos.get("tier2_exited", False):
            exit_decisions.append({
                "token_mint": mint, "symbol": pos["token_symbol"],
                "reason": f"TP tier 2: {pnl_pct:.1f}% (tier={tier['label']}, target={tier['tp2_pnl']}%)",
                "exit_pct": tier["tp2_sell"], "urgency": "normal",
            })
            pos["tier2_exited"] = True
        elif pnl_pct > 0 and peak_drawdown_pct <= -tier["trail_pct"]:
            exit_decisions.append({
                "token_mint": mint, "symbol": pos["token_symbol"],
                "reason": f"Trailing stop: {peak_drawdown_pct:.1f}% from peak (tier={tier['label']}, trail={tier['trail_pct']}%)",
                "exit_pct": 100, "urgency": "high",
            })
        elif age_minutes >= tier["decay_min"] and abs(pnl_pct) < 5:
            exit_decisions.append({
                "token_mint": mint, "symbol": pos["token_symbol"],
                "reason": f"Time decay: {age_minutes:.0f}min, {pnl_pct:.1f}% PnL (tier={tier['label']}, limit={tier['decay_min']}min)",
                "exit_pct": 100, "urgency": "low",
            })
        elif pos.get("entry_liquidity") and liquidity < pos["entry_liquidity"] * 0.5:
            exit_decisions.append({
                "token_mint": mint, "symbol": pos["token_symbol"],
                "reason": f"Liquidity drop: ${liquidity:,.0f} (was ${pos['entry_liquidity']:,.0f})",
                "exit_pct": 100, "urgency": "high",
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
        return "FAIL"


async def scan_token_narrative(
    mint: str,
    birdeye: BirdeyeClient,
    tracker: NarrativeTracker,
) -> dict[str, Any] | None:
    """Scan single token for narrative signals (on-chain volume only)."""
    try:
        overview = await birdeye.get_token_overview(mint)
        data = overview.get("data", overview)
        symbol = data.get("symbol", "UNKNOWN")

        volume_1h = float(data.get("v1hUSD", 0))
        volume_24h = float(data.get("v24hUSD", 0))
        avg_hourly = volume_24h / 24 if volume_24h > 0 else 0
        volume_ratio = round(volume_1h / avg_hourly, 1) if avg_hourly > 0 else 0

        if volume_ratio >= 2.0:
            tracker.record_detection(mint)

        return {
            "token_mint": mint,
            "token_symbol": symbol,
            "x_mentions_1h": 0,
            "kol_mentions": 0,
            "volume_vs_avg": f"{volume_ratio}x",
        }
    except Exception as e:
        _record_error(None, "narrative_hunter", e, {"token_mint": mint, "detail": "scan_token_narrative failed"})
        return None


async def main():
    result = await run_heartbeat()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
