"""Mock Nansen API responses for testing."""

from __future__ import annotations

# Smart money transactions showing whale accumulation (legacy dex-trades format)
SMART_MONEY_TRANSACTIONS = {
    "data": [
        {"token_sold_address": "So11111111111111111111111111111111111111112", "token_bought_address": "BOAR111", "token_bought_symbol": "BOAR", "trader_address": "whale1", "trade_value_usd": 5000},
        {"token_sold_address": "So11111111111111111111111111111111111111112", "token_bought_address": "BOAR111", "token_bought_symbol": "BOAR", "trader_address": "whale2", "trade_value_usd": 8000},
        {"token_sold_address": "So11111111111111111111111111111111111111112", "token_bought_address": "BOAR111", "token_bought_symbol": "BOAR", "trader_address": "whale3", "trade_value_usd": 3000},
        {"token_sold_address": "So11111111111111111111111111111111111111112", "token_bought_address": "BOAR111", "token_bought_symbol": "BOAR", "trader_address": "whale4", "trade_value_usd": 12000},
        # Second token — only 2 wallets (below threshold)
        {"token_sold_address": "So11111111111111111111111111111111111111112", "token_bought_address": "WEAK222", "token_bought_symbol": "WEAK", "trader_address": "walletA", "trade_value_usd": 2000},
        {"token_sold_address": "So11111111111111111111111111111111111111112", "token_bought_address": "WEAK222", "token_bought_symbol": "WEAK", "trader_address": "walletB", "trade_value_usd": 1500},
        # Sell signals (should be filtered out — SOL is token_bought)
        {"token_sold_address": "DUMP333", "token_bought_address": "So11111111111111111111111111111111111111112", "token_bought_symbol": "SOL", "trader_address": "dumper1", "trade_value_usd": 50000},
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

# --- TGM endpoint mocks ---

# Token Screener response
TOKEN_SCREENER_RESPONSE = {
    "data": [
        {
            "token_address": "ALPHA111",
            "symbol": "ALPHA",
            "smart_money_wallets": 7,
            "smart_money_inflow_usd": 185000,
        },
        {
            "token_address": "BETA222",
            "symbol": "BETA",
            "smart_money_wallets": 4,
            "smart_money_inflow_usd": 62000,
        },
        {
            "token_address": "GAMMA333",
            "symbol": "GAMMA",
            "smart_money_wallets": 3,
            "smart_money_inflow_usd": 31000,
        },
    ]
}

# Flow Intelligence response
FLOW_INTELLIGENCE_RESPONSE = {
    "data": {
        "smart_trader_net_usd": 45000,
        "whale_net_usd": 120000,
        "exchange_net_usd": -35000,
        "fresh_wallet_net_usd": 8000,
        "top_pnl_net_usd": 22000,
    }
}

# Flow Intelligence with exchange inflow (distribution pattern)
FLOW_INTELLIGENCE_EXCHANGE_INFLOW = {
    "data": {
        "smart_trader_net_usd": 5000,
        "whale_net_usd": 10000,
        "exchange_net_usd": 75000,
        "fresh_wallet_net_usd": 2000,
        "top_pnl_net_usd": 1000,
    }
}

# Flow Intelligence with high fresh wallet inflow (red flag)
FLOW_INTELLIGENCE_FRESH_WALLET = {
    "data": {
        "smart_trader_net_usd": 10000,
        "whale_net_usd": 15000,
        "exchange_net_usd": -5000,
        "fresh_wallet_net_usd": 85000,
        "top_pnl_net_usd": 3000,
    }
}

# Who Bought/Sold response
WHO_BOUGHT_SOLD_RESPONSE = {
    "data": {
        "smart_money_buyers": 5,
        "total_buy_volume_usd": 142000,
        "smart_money_sellers": 1,
        "total_sell_volume_usd": 18000,
    }
}

# Jupiter DCAs response
JUPITER_DCAS_RESPONSE = {
    "data": [
        {"wallet": "whale1aaa", "amount_usd": 5000, "interval": "1h", "remaining_orders": 12},
        {"wallet": "whale3ccc", "amount_usd": 2000, "interval": "4h", "remaining_orders": 6},
        {"wallet": "whale5eee", "amount_usd": 1000, "interval": "1d", "remaining_orders": 30},
    ]
}

# Empty DCAs (no active orders)
JUPITER_DCAS_EMPTY = {
    "data": []
}

# Smart Money Holdings response
SMART_MONEY_HOLDINGS_RESPONSE = {
    "data": [
        {"token_address": "ALPHA111", "symbol": "ALPHA", "balance_change_24h": 250000},
        {"token_address": "DELTA444", "symbol": "DELTA", "balance_change_24h": 80000},
        {"token_address": "BETA222", "symbol": "BETA", "balance_change_24h": 15000},
        {"token_address": "EPSILON555", "symbol": "EPS", "balance_change_24h": -120000},
    ]
}
