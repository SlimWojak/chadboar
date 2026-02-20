"""Paper Trade Logger — phantom trade tracking for calibration.

Logs phantom entries at current price, tracks PnL over time windows,
and writes beads to the flight recorder for analysis.

Usage:
    python3 -m lib.skills.paper_trade --log '{"token_mint": "...", "token_symbol": "...", ...}'
    python3 -m lib.skills.paper_trade --check
    python3 -m lib.skills.paper_trade --digest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PAPER_FILE = WORKSPACE / "state" / "paper_trades.json"


def _load_trades() -> list[dict[str, Any]]:
    if PAPER_FILE.exists():
        return json.loads(PAPER_FILE.read_text())
    return []


def _save_trades(trades: list[dict[str, Any]]) -> None:
    PAPER_FILE.write_text(json.dumps(trades, indent=2))


def log_paper_trade(candidate: dict[str, Any]) -> dict[str, Any]:
    """Log a phantom entry for a candidate that scored PAPER_TRADE (40-59)."""
    now = datetime.now(timezone.utc)
    entry_price = float(candidate.get("price_usd", 0))
    if entry_price == 0:
        # Try to derive from fdv/mcap
        fdv = float(candidate.get("fdv", candidate.get("market_cap", 0)))
        entry_price = fdv  # Use FDV as proxy

    score_data = candidate.get("score", {})

    trade = {
        "id": now.strftime("%Y%m%d_%H%M%S") + f"_paper_{candidate.get('token_symbol', 'UNK')}",
        "token_mint": candidate.get("token_mint", ""),
        "token_symbol": candidate.get("token_symbol", ""),
        "entry_time": now.isoformat(),
        "entry_epoch": int(now.timestamp()),
        "entry_price_fdv": entry_price,
        "entry_liquidity": float(candidate.get("liquidity_usd", 0)),
        "entry_volume": float(candidate.get("volume_usd", 0)),
        "source": candidate.get("source", "unknown"),
        "discovery_source": candidate.get("discovery_source", "unknown"),
        "play_type": score_data.get("play_type", "unknown"),
        "permission_score": score_data.get("permission_score", 0),
        "ordering_score": score_data.get("ordering_score", 0),
        "recommendation": score_data.get("recommendation", "PAPER_TRADE"),
        "breakdown": score_data.get("breakdown", {}),
        "red_flags": score_data.get("red_flags", {}),
        "warden_verdict": candidate.get("warden", {}).get("verdict", "UNKNOWN"),
        "verdict_bead_id": candidate.get("verdict_bead_id", ""),
        "trade_bead_id": "",
        "pnl_checks": [],
        "closed": False,
    }

    trades = _load_trades()
    trades.append(trade)
    _save_trades(trades)

    return trade


def update_trade_bead_id(trade_id: str, trade_bead_id: str, verdict_bead_id: str = "") -> None:
    """Patch a paper trade record with structured bead IDs."""
    trades = _load_trades()
    for t in trades:
        if t["id"] == trade_id:
            t["trade_bead_id"] = trade_bead_id
            if verdict_bead_id:
                t["verdict_bead_id"] = verdict_bead_id
            break
    _save_trades(trades)


async def check_paper_trades(bead_chain: Any = None) -> dict[str, Any]:
    """Check current prices for open paper trades and record PnL snapshots."""
    from lib.clients.birdeye import BirdeyeClient

    trades = _load_trades()
    open_trades = [t for t in trades if not t.get("closed")]

    if not open_trades:
        return {"status": "OK", "message": "No open paper trades", "open": 0, "checked": 0}

    birdeye = BirdeyeClient()
    checked = 0
    expired = 0
    now = datetime.now(timezone.utc)
    now_epoch = int(now.timestamp())

    # Batch-close stale trades (>6h) without API calls
    closed_trades = []
    for trade in open_trades:
        age_minutes = (now_epoch - trade["entry_epoch"]) / 60
        if age_minutes >= 360:
            trade["closed"] = True
            trade["close_reason"] = "6h_expiry"
            expired += 1
            closed_trades.append(trade)

    # Check PnL on the 10 most recent still-open trades (cap API calls)
    still_open = [t for t in open_trades if not t.get("closed")]
    recent = sorted(still_open, key=lambda t: t["entry_epoch"], reverse=True)[:10]

    try:
        for trade in recent:
            age_minutes = (now_epoch - trade["entry_epoch"]) / 60

            try:
                overview = await birdeye.get_token_overview(trade["token_mint"])
                data = overview.get("data", overview)
                current_fdv = float(data.get("fdv", data.get("mc", 0)))
                current_price = float(data.get("price", 0))
                current_liq = float(data.get("liquidity", 0))

                entry_fdv = trade["entry_price_fdv"]
                pnl_pct = ((current_fdv - entry_fdv) / entry_fdv * 100) if entry_fdv > 0 else 0

                snapshot = {
                    "time": now.isoformat(),
                    "age_minutes": round(age_minutes, 1),
                    "current_fdv": current_fdv,
                    "current_price": current_price,
                    "current_liq": current_liq,
                    "pnl_pct": round(pnl_pct, 2),
                }
                trade.setdefault("pnl_checks", []).append(snapshot)
                checked += 1

            except Exception:
                pass  # Token may have been rugged/delisted

        _save_trades(trades)

        # Emit autopsy beads for closed trades (v0.2 — best-effort)
        autopsies = 0
        if bead_chain and closed_trades:
            try:
                from lib.beads.emitters import emit_autopsy_bead
                for trade in closed_trades:
                    t_bead_id = trade.get("trade_bead_id", "")
                    if not t_bead_id:
                        continue

                    checks = trade.get("pnl_checks", [])
                    final_pnl = checks[-1]["pnl_pct"] if checks else 0.0
                    exit_fdv = checks[-1].get("current_fdv", 0) if checks else 0.0
                    hold_secs = now_epoch - trade.get("entry_epoch", now_epoch)

                    try:
                        _aid = emit_autopsy_bead(
                            bead_chain,
                            trade_bead_id=t_bead_id,
                            token_mint=trade.get("token_mint", ""),
                            token_symbol=trade.get("token_symbol", "?"),
                            pnl_pct=final_pnl,
                            exit_price=exit_fdv,
                            exit_reason=trade.get("close_reason", "6h_expiry"),
                            hold_duration_seconds=hold_secs,
                            lesson=f"Paper {'win' if final_pnl >= 0 else 'loss'}: "
                                   f"{trade.get('token_symbol', '?')} "
                                   f"{final_pnl:+.1f}% over {hold_secs // 60}min",
                            supports_thesis=(final_pnl >= 0),
                        )
                        if _aid:
                            autopsies += 1
                    except Exception:
                        pass
            except Exception:
                pass

        return {
            "status": "OK", "open": len(still_open), "checked": checked,
            "expired": expired, "autopsies": autopsies,
        }
    finally:
        await birdeye.close()


def get_digest() -> dict[str, Any]:
    """Generate a paper trading digest for reporting."""
    trades = _load_trades()

    if not trades:
        return {
            "status": "OK",
            "total": 0,
            "message": "No paper trades recorded yet",
        }

    closed = [t for t in trades if t.get("closed")]
    open_trades = [t for t in trades if not t.get("closed")]

    profitable = 0
    best_trade = None
    best_pnl = -999
    worst_pnl = 999
    total_pnl = 0

    for t in closed:
        pnl = t.get("final_pnl_pct", 0)
        total_pnl += pnl
        if pnl > 0:
            profitable += 1
        if pnl > best_pnl:
            best_pnl = pnl
            best_trade = t.get("token_symbol", "?")
        if pnl < worst_pnl:
            worst_pnl = pnl

    # Also check current open trade PnL
    for t in open_trades:
        checks = t.get("pnl_checks", [])
        if checks:
            latest = checks[-1]
            pnl = latest.get("pnl_pct", 0)
            if pnl > best_pnl:
                best_pnl = pnl
                best_trade = t.get("token_symbol", "?") + " (open)"

    return {
        "status": "OK",
        "total": len(trades),
        "open": len(open_trades),
        "closed": len(closed),
        "profitable": profitable,
        "win_rate": f"{profitable / len(closed) * 100:.0f}%" if closed else "N/A",
        "avg_pnl": f"{total_pnl / len(closed):.1f}%" if closed else "N/A",
        "best_trade": f"{best_trade} +{best_pnl:.1f}%" if best_trade else "N/A",
        "worst_pnl": f"{worst_pnl:.1f}%" if closed else "N/A",
        "summary": f"Paper trades: {len(trades)} candidates, {profitable} profitable"
            + (f", best was {best_trade} at +{best_pnl:.1f}%" if best_trade and best_pnl > 0 else ""),
    }


def write_paper_bead(trade: dict[str, Any]) -> None:
    """Write a paper trade bead to the flight recorder."""
    from lib.edge.bank import Bead, EdgeBank

    bank = EdgeBank()
    bead = Bead(
        bead_type="paper_trade",
        token_mint=trade.get("token_mint", ""),
        token_symbol=trade.get("token_symbol", ""),
        direction="buy",
        amount_sol=0.0,  # Phantom — no real capital
        price_usd=trade.get("entry_price_fdv", 0),
        thesis=f"Paper trade: score={trade.get('permission_score', 0)} "
               f"play={trade.get('play_type', '?')} "
               f"source={trade.get('discovery_source', '?')}",
        signals=[trade.get("breakdown", {})],
        score=trade.get("permission_score", 0),
        warden_verdict=trade.get("warden_verdict", "UNKNOWN"),
        extra={"paper": True, "red_flags": trade.get("red_flags", {})},
    )
    bank.write_bead(bead)


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Trade Logger")
    parser.add_argument("--log", help="Log a paper trade (JSON candidate)")
    parser.add_argument("--check", action="store_true", help="Check PnL of open trades")
    parser.add_argument("--digest", action="store_true", help="Show paper trading digest")
    args = parser.parse_args()

    if args.log:
        candidate = json.loads(args.log)
        result = log_paper_trade(candidate)
        print(json.dumps(result, indent=2))
    elif args.check:
        result = asyncio.run(check_paper_trades())
        print(json.dumps(result, indent=2))
    elif args.digest:
        result = get_digest()
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
