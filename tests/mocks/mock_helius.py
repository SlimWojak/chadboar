"""Mock Helius API responses for testing."""

from __future__ import annotations

TOKEN_METADATA = [
    {
        "account": "BOAR11111111111111111111111111111111111111",
        "onChainMetadata": {
            "metadata": {
                "data": {
                    "name": "AutistBoar Token",
                    "symbol": "BOAR",
                },
                "mint": "BOAR11111111111111111111111111111111111111",
            }
        },
    }
]

SIMULATE_SUCCESS = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "err": None,
        "logs": ["Program log: Instruction: Swap", "Program log: Success"],
    },
}

SIMULATE_FAILURE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "err": {"InstructionError": [0, "Custom(6001)"]},
        "logs": ["Program log: Error: Insufficient funds"],
    },
}
