"""Mock Nansen API responses for testing."""

from __future__ import annotations

# Smart money transactions showing whale accumulation
SMART_MONEY_TRANSACTIONS = {
    "data": [
        {"token_address": "BOAR111", "token_symbol": "BOAR", "wallet_address": "whale1", "type": "buy", "value_usd": 5000},
        {"token_address": "BOAR111", "token_symbol": "BOAR", "wallet_address": "whale2", "type": "buy", "value_usd": 8000},
        {"token_address": "BOAR111", "token_symbol": "BOAR", "wallet_address": "whale3", "type": "buy", "value_usd": 3000},
        {"token_address": "BOAR111", "token_symbol": "BOAR", "wallet_address": "whale4", "type": "buy", "value_usd": 12000},
        # Second token — only 2 wallets (below threshold)
        {"token_address": "WEAK222", "token_symbol": "WEAK", "wallet_address": "walletA", "type": "buy", "value_usd": 2000},
        {"token_address": "WEAK222", "token_symbol": "WEAK", "wallet_address": "walletB", "type": "buy", "value_usd": 1500},
        # Sell signals (should be filtered out)
        {"token_address": "DUMP333", "token_symbol": "DUMP", "wallet_address": "dumper1", "type": "sell", "value_usd": 50000},
    ]
}

# Token-specific smart money data
TOKEN_SMART_MONEY = {
    "data": [
        {"address": "whale1aaa", "label": "Smart Money #1", "pnl_usd": 150000},
        {"address": "whale2bbb", "label": "Smart Money #2", "pnl_usd": 85000},
        {"address": "whale3ccc", "label": "Degen Fund", "pnl_usd": 42000},
        {"address": "whale4ddd", "label": "MEV Bot", "pnl_usd": 200000},
        {"address": "whale5eee", "label": "Unknown", "pnl_usd": 15000},
    ]
}
