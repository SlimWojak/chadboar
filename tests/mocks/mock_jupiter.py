"""Mock Jupiter API responses for testing."""

from __future__ import annotations

# Buy quote: 0.5 SOL → some token
BUY_QUOTE = {
    "inputMint": "So11111111111111111111111111111111111111112",
    "inAmount": "500000000",  # 0.5 SOL in lamports
    "outputMint": "BOAR11111111111111111111111111111111111111",
    "outAmount": "1000000000",  # 1B token units
    "otherAmountThreshold": "970000000",
    "swapMode": "ExactIn",
    "slippageBps": 300,
    "priceImpactPct": "0.15",
    "routePlan": [
        {"swapInfo": {"label": "Raydium"}, "percent": 100},
    ],
}

# Sell quote: tokens → SOL
SELL_QUOTE = {
    "inputMint": "BOAR11111111111111111111111111111111111111",
    "inAmount": "1000000000",
    "outputMint": "So11111111111111111111111111111111111111112",
    "outAmount": "750000000",  # 0.75 SOL — profit
    "otherAmountThreshold": "727500000",
    "swapMode": "ExactIn",
    "slippageBps": 300,
    "priceImpactPct": "0.22",
    "routePlan": [
        {"swapInfo": {"label": "Orca"}, "percent": 60},
        {"swapInfo": {"label": "Raydium"}, "percent": 40},
    ],
}

# Swap transaction response
SWAP_TX = {
    "swapTransaction": "AQAAAAAAAAAAAAAAAAAAAAAAAAAA==",  # Fake base64
    "lastValidBlockHeight": 123456789,
}
