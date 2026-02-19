"""Bead schema — Pydantic v2 strict models for the intelligence substrate.

Every bead has three layers:
  1. Header — identity, type, chain link, timing, agent provenance
  2. Edges — derived_from, supports, contradicts (mandatory graph structure)
  3. Payload — type-specific data

Bead IDs are hex-encoded SHA-256 of canonical JSON content (deterministic).
Edges are mandatory because isolated knowledge doesn't compound — only
connected knowledge does.

Ports to a8ra: agent_id becomes per-agent, Gate-signing replaces self-signing,
cross-agent edges enable multi-agent intelligence fusion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


class BeadType(str, Enum):
    """Exhaustive set of bead types."""

    SIGNAL = "signal"
    VERDICT = "verdict"
    TRADE = "trade"
    AUTOPSY = "autopsy"
    INSIGHT = "insight"
    HEARTBEAT = "heartbeat"


# ── Null sentinel for genesis bead ──────────────────────────────────
GENESIS_PREV_HASH = "0" * 64


# ── Header ──────────────────────────────────────────────────────────

class BeadHeader(BaseModel):
    """Identity and chain linkage. Present on every bead."""

    bead_id: str = Field(
        default="",
        description="SHA-256 of canonical content. Computed at write time.",
    )
    bead_type: BeadType
    prev_hash: str = Field(
        default=GENESIS_PREV_HASH,
        description="Hash of previous bead in chain. Genesis uses null sentinel.",
    )
    timestamp: str = Field(
        default="",
        description="ISO-8601 UTC. Set at creation if empty.",
    )
    agent_id: str = Field(
        default="chadboar-v0.2",
        description="Parameterized for a8ra multi-agent.",
    )
    session_id: str = Field(
        default="",
        description="OpenClaw cron run ID. Ties to canary for execution verification.",
    )


# ── Edges ───────────────────────────────────────────────────────────

class BeadEdges(BaseModel):
    """Mandatory graph structure. Every bead must declare its lineage.

    derived_from: what inputs informed this bead (min 1, except heartbeat)
    supports: beads whose findings this bead agrees with
    contradicts: beads whose findings this bead disagrees with
    edges_complete: honest self-declaration of edge population quality
    """

    derived_from: list[str] = Field(
        default_factory=list,
        description="Bead IDs of inputs. Min 1 for all types except heartbeat.",
    )
    supports: list[str] = Field(
        default_factory=list,
        description="Bead IDs this bead's findings agree with.",
    )
    contradicts: list[str] = Field(
        default_factory=list,
        description="Bead IDs this bead's findings disagree with.",
    )
    edges_complete: bool = Field(
        default=True,
        description="Agent self-declares whether edges are fully populated.",
    )
    edges_incomplete_reason: str = Field(
        default="",
        description="Why edges are incomplete. Required when edges_complete=False.",
    )


# ── Provenance ──────────────────────────────────────────────────────

class BeadProvenance(BaseModel):
    """Data lineage and verifiability metadata."""

    data_sources: dict[str, str] = Field(
        default_factory=dict,
        description='Per-source status. e.g. {"dexscreener": "OK", "birdeye": "SKIP"}',
    )
    source_hash: str = Field(
        default="",
        description="SHA-256 of the raw input data that produced this bead.",
    )
    attestation_coverage: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of inputs that are independently verifiable (0.0-1.0).",
    )


# ── Payloads (type-specific) ───────────────────────────────────────

class SignalPayload(BaseModel):
    """A candidate signal entering the scoring funnel."""

    token_mint: str
    token_symbol: str
    play_type: Literal["graduation", "accumulation"]
    discovery_source: str = Field(
        description="Where the signal originated (e.g. 'pulse-bonding', 'dex-trades').",
    )
    raw_metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Actual numbers: volume, FDV, liquidity, holder count, etc.",
    )


class VerdictPayload(BaseModel):
    """A scored candidate with conviction breakdown."""

    token_mint: str
    token_symbol: str
    play_type: Literal["graduation", "accumulation"]
    scoring_breakdown: dict[str, Any] = Field(
        default_factory=dict,
        description="Full weighted scores per component.",
    )
    conviction_score: int = Field(ge=0, le=100)
    recommendation: Literal[
        "AUTO_EXECUTE", "WATCHLIST", "PAPER_TRADE", "DISCARD", "VETO"
    ]
    warden_verdict: Literal["PASS", "WARN", "FAIL", "UNKNOWN"]
    red_flags: dict[str, Any] = Field(default_factory=dict)


class TradePayload(BaseModel):
    """An executed trade (live or paper)."""

    token_mint: str
    token_symbol: str
    play_type: Literal["graduation", "accumulation"]
    scoring_breakdown: dict[str, Any] = Field(default_factory=dict)
    conviction_score: int = Field(ge=0, le=100)
    recommendation: str
    warden_verdict: str
    red_flags: dict[str, Any] = Field(default_factory=dict)
    entry_price: float
    entry_amount_sol: float
    entry_tx_hash: str = Field(
        default="",
        description="Empty for paper trades.",
    )
    gate: Literal["auto", "escalated", "human_approved", "paper"]


class AutopsyPayload(BaseModel):
    """Post-trade evaluation with PnL and reflection."""

    trade_bead_id: str = Field(
        description="References the trade bead being evaluated.",
    )
    pnl_sol: float = 0.0
    pnl_pct: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_tx_hash: str = Field(
        default="",
        description="Empty for paper trades.",
    )
    hold_duration_seconds: int = 0
    lesson: str = Field(
        default="",
        description="One-sentence agent reflection on what happened.",
    )


class InsightPayload(BaseModel):
    """Distilled intelligence from pattern mining."""

    insight_type: Literal["pattern", "failure_mode", "regime_shift", "skill_extracted"]
    content: str = Field(description="The distilled insight.")
    evidence_bead_ids: list[str] = Field(
        default_factory=list,
        description="Beads that support this insight.",
    )
    confidence: float = Field(ge=0.0, le=1.0)


class HeartbeatPayload(BaseModel):
    """Cycle-level metadata for pipeline health tracking."""

    cycle_number: int
    signals_found: int = 0
    signals_vetoed: int = 0
    signals_passed: int = 0
    pot_sol: float = 0.0
    positions_count: int = 0
    pipeline_health: dict[str, str] = Field(
        default_factory=dict,
        description="Per-source status map.",
    )
    canary_hash: str = Field(
        default="",
        description="Ties to canary file for hallucination detection.",
    )


# ── Unified Bead ────────────────────────────────────────────────────

# Payload union type
PayloadType = Union[
    SignalPayload,
    VerdictPayload,
    TradePayload,
    AutopsyPayload,
    InsightPayload,
    HeartbeatPayload,
]


class Bead(BaseModel):
    """A single bead in the intelligence chain.

    Three layers: header (identity + chain), edges (graph), payload (data).
    Bead ID is computed from canonical content at write time.
    """

    header: BeadHeader
    edges: BeadEdges = Field(default_factory=BeadEdges)
    provenance: BeadProvenance = Field(default_factory=BeadProvenance)
    payload: PayloadType

    @model_validator(mode="after")
    def _validate_edges(self) -> "Bead":
        """Enforce edge discipline per bead type."""
        bead_type = self.header.bead_type

        # Heartbeat beads derive from previous heartbeat (set by chain manager)
        # All other types need at least one derived_from edge
        if bead_type != BeadType.HEARTBEAT and not self.edges.derived_from:
            self.edges.edges_complete = False
            if not self.edges.edges_incomplete_reason:
                self.edges.edges_incomplete_reason = "no derived_from edges declared"

        # Autopsy beads must reference a trade bead in derived_from
        if bead_type == BeadType.AUTOPSY:
            assert isinstance(self.payload, AutopsyPayload)
            trade_ref = self.payload.trade_bead_id
            if trade_ref and trade_ref not in self.edges.derived_from:
                self.edges.derived_from.append(trade_ref)
            # Autopsy MUST support or contradict the original verdict.
            # This is the most valuable edge for SkillRL distillation —
            # "I predicted X, reality was Y, was I right?"
            if not self.edges.supports and not self.edges.contradicts:
                raise ValueError(
                    "Autopsy bead must declare at least one 'supports' or "
                    "'contradicts' edge referencing the original verdict. "
                    "This edge is required for the learning loop."
                )

        return self

    @model_validator(mode="after")
    def _set_timestamp(self) -> "Bead":
        """Set timestamp if not already set."""
        if not self.header.timestamp:
            self.header.timestamp = datetime.now(timezone.utc).isoformat()
        return self

    def canonical_content(self) -> str:
        """Produce deterministic JSON for hashing.

        Excludes bead_id and prev_hash (these are chain metadata, not content).
        Two beads with identical content produce identical hashes.
        """
        content = {
            "bead_type": self.header.bead_type.value,
            "timestamp": self.header.timestamp,
            "agent_id": self.header.agent_id,
            "session_id": self.header.session_id,
            "edges": self.edges.model_dump(mode="json"),
            "provenance": self.provenance.model_dump(mode="json"),
            "payload": self.payload.model_dump(mode="json"),
        }
        return json.dumps(content, sort_keys=True, separators=(",", ":"))

    def compute_bead_id(self) -> str:
        """SHA-256 of canonical content."""
        return hashlib.sha256(self.canonical_content().encode()).hexdigest()

    def to_chain_dict(self) -> dict[str, Any]:
        """Full bead as a dict for storage/export."""
        return {
            "header": self.header.model_dump(mode="json"),
            "edges": self.edges.model_dump(mode="json"),
            "provenance": self.provenance.model_dump(mode="json"),
            "payload": self.payload.model_dump(mode="json"),
            "payload_type": self.header.bead_type.value,
        }

    @classmethod
    def from_chain_dict(cls, data: dict[str, Any]) -> "Bead":
        """Reconstruct a Bead from stored dict."""
        payload_type_map: dict[str, type[PayloadType]] = {
            "signal": SignalPayload,
            "verdict": VerdictPayload,
            "trade": TradePayload,
            "autopsy": AutopsyPayload,
            "insight": InsightPayload,
            "heartbeat": HeartbeatPayload,
        }
        bead_type_str = data.get("payload_type", data["header"]["bead_type"])
        payload_cls = payload_type_map[bead_type_str]
        return cls(
            header=BeadHeader(**data["header"]),
            edges=BeadEdges(**data["edges"]),
            provenance=BeadProvenance(**data["provenance"]),
            payload=payload_cls(**data["payload"]),
        )
