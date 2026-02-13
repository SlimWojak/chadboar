#!/usr/bin/env python3
"""
Red Flag Detection — Negative evidence weighting.

Detects:
1. Concentrated volume (top 3 trades >70% of 1h volume)
2. Dumper wallets (whales with ≥2 fast dumps <30min in last 7 days)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def check_concentrated_volume(trades_data: dict[str, Any]) -> tuple[bool, str]:
    """Check if volume is concentrated in few large trades.
    
    Args:
        trades_data: Response from birdeye.get_trades()
    
    Returns:
        (is_concentrated, reason)
    """
    trades = trades_data.get("data", {}).get("items", [])
    if not trades or len(trades) < 3:
        return False, "Insufficient trade data"
    
    # Get trades from last 1 hour
    now = datetime.utcnow()
    one_hour_ago = now - timedelta(hours=1)
    
    recent_trades = []
    for trade in trades:
        timestamp = trade.get("blockUnixTime", 0)
        trade_time = datetime.utcfromtimestamp(timestamp)
        if trade_time >= one_hour_ago:
            recent_trades.append(trade)
    
    if len(recent_trades) < 3:
        return False, f"Only {len(recent_trades)} trades in last hour"
    
    # Calculate total volume and top 3 trades
    trade_volumes = []
    for trade in recent_trades:
        volume_usd = float(trade.get("volumeInUSD", 0))
        trade_volumes.append(volume_usd)
    
    trade_volumes.sort(reverse=True)
    top_3_volume = sum(trade_volumes[:3])
    total_volume = sum(trade_volumes)
    
    if total_volume == 0:
        return False, "Zero volume in last hour"
    
    concentration_pct = (top_3_volume / total_volume) * 100
    
    if concentration_pct > 70:
        return True, f"Top 3 trades = {concentration_pct:.1f}% of 1h volume ({len(recent_trades)} total trades)"
    
    return False, f"Top 3 trades = {concentration_pct:.1f}% of volume (distributed)"


def check_dumper_wallets(
    wallets: list[str],
    nansen_client: Any,  # NansenClient
) -> tuple[int, str]:
    """Check how many whales have dumper patterns.
    
    Args:
        wallets: List of whale wallet addresses
        nansen_client: NansenClient instance for fetching history
    
    Returns:
        (dumper_count, reason_string)
    """
    # For now, return stub implementation
    # Full implementation requires async calls to nansen_client.get_wallet_transaction_history()
    # and analyzing buy->sell pairs with <30min hold time
    
    # TODO: Implement full dumper detection in async context
    return 0, "Dumper detection not yet implemented"


def analyze_dumper_history(tx_history: dict[str, Any]) -> bool:
    """Analyze wallet transaction history for fast dump pattern.
    
    A wallet is flagged as dumper if it has ≥2 instances of:
    - Buy token
    - Sell same token within 30 minutes
    
    Args:
        tx_history: Response from nansen.get_wallet_transaction_history()
    
    Returns:
        True if wallet is a dumper
    """
    transactions = tx_history.get("data", [])
    if not isinstance(transactions, list):
        return False
    
    # Track token holdings and buy times
    token_buys = {}  # {token_mint: [buy_timestamps]}
    fast_dumps = 0
    
    for tx in sorted(transactions, key=lambda t: t.get("block_timestamp", 0)):
        tx_type = tx.get("tx_type", "")
        token = tx.get("token_address", "")
        timestamp = tx.get("block_timestamp", 0)
        
        if not token or not timestamp:
            continue
        
        tx_time = datetime.utcfromtimestamp(timestamp)
        
        if tx_type == "buy" or tx_type == "swap_in":
            # Record buy time
            if token not in token_buys:
                token_buys[token] = []
            token_buys[token].append(tx_time)
        
        elif tx_type == "sell" or tx_type == "swap_out":
            # Check if there's a recent buy
            if token in token_buys and token_buys[token]:
                for buy_time in token_buys[token]:
                    hold_duration = (tx_time - buy_time).total_seconds() / 60
                    if 0 < hold_duration <= 30:
                        fast_dumps += 1
                        # Remove this buy time so we don't double-count
                        token_buys[token].remove(buy_time)
                        break
    
    return fast_dumps >= 2
