"""Mock Birdeye API responses for testing."""

from __future__ import annotations

# Clean token — should PASS rug warden
CLEAN_TOKEN_OVERVIEW = {
    "data": {
        "address": "CLEANmint111111111111111111111111111111111",
        "symbol": "CLEAN",
        "name": "Clean Token",
        "liquidity": 85000,
        "v1hUSD": 45000,
        "v24hUSD": 280000,
        "holder": 1250,
        "mc": 2500000,
        "createdAt": 1707500000000,  # ~Feb 2024 — old enough
    }
}

CLEAN_TOKEN_SECURITY = {
    "data": {
        "top10HolderPercent": 0.35,
        "isMintable": False,
        "isFreezable": False,
        "isLpLocked": True,
        "isLpBurned": False,
    }
}

# Rug token — should FAIL rug warden
RUG_TOKEN_OVERVIEW = {
    "data": {
        "address": "RUGmint2222222222222222222222222222222222222",
        "symbol": "RUGPULL",
        "name": "Rug Pull Token",
        "liquidity": 500,  # Way below $10k minimum
        "v1hUSD": 200,
        "v24hUSD": 800,
        "holder": 8,
        "mc": 5000,
        "createdAt": 1707580000000,
    }
}

RUG_TOKEN_SECURITY = {
    "data": {
        "top10HolderPercent": 0.95,  # 95% — way above 80% threshold
        "isMintable": True,          # Mutable mint — FAIL
        "isFreezable": True,         # Mutable freeze — FAIL
        "isLpLocked": False,
        "isLpBurned": False,
    }
}

# Warning token — should WARN
WARN_TOKEN_OVERVIEW = {
    "data": {
        "address": "WARNmint333333333333333333333333333333333333",
        "symbol": "FRESH",
        "name": "Fresh Token",
        "liquidity": 25000,
        "v1hUSD": 12000,
        "v24hUSD": 50000,
        "holder": 180,
        "mc": 500000,
        "createdAt": int(__import__("time").time() * 1000) - 120000,  # 2 min ago — very new
    }
}

WARN_TOKEN_SECURITY = {
    "data": {
        "top10HolderPercent": 0.55,
        "isMintable": False,
        "isFreezable": False,
        "isLpLocked": False,  # LP not locked → WARN
        "isLpBurned": False,
    }
}
