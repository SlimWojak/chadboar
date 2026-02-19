"""Bead system — structured intelligence substrate.

The bead chain is ChadBoar's memory. Every signal, verdict, trade, and
reflection is captured as a hash-linked bead with mandatory provenance
edges. This module provides schema validation (Pydantic v2 strict) and
an append-only SQLite chain manager.

Designed for portability to a8ra's multi-agent system.
"""

from lib.beads.schema import (
    BeadType,
    BeadHeader,
    BeadEdges,
    BeadProvenance,
    SignalPayload,
    VerdictPayload,
    TradePayload,
    AutopsyPayload,
    InsightPayload,
    HeartbeatPayload,
    Bead,
)
from lib.beads.chain import BeadChain

__all__ = [
    "BeadType",
    "BeadHeader",
    "BeadEdges",
    "BeadProvenance",
    "SignalPayload",
    "VerdictPayload",
    "TradePayload",
    "AutopsyPayload",
    "InsightPayload",
    "HeartbeatPayload",
    "Bead",
    "BeadChain",
]
