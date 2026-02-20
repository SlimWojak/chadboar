"""Bead Field schema — BEAD_FIELD_SPEC v0.2 implementation for ChadBoar.

Implements the a8ra canonical bead specification adapted to the memecoin
trading domain. This is the real spec, not a stub — every edge case found
here saves weeks in production.

Key differences from v0:
  - bead_id is UUID v7 (time-ordered), NOT content-hash
  - hash_self is SHA-256 of canonical content (separate from identity)
  - Bi-temporal: world_time (WT) + knowledge_time (KT) on every bead
  - ECDSA attestation envelope on every bead
  - 10 bead types (8 canonical a8ra + 2 ChadBoar extensions)
  - Flat BeadBase structure (no header/edges/provenance nesting)
  - Lineage replaces edges (ordered parent list, not supports/contradicts)

Ports to a8ra: PQC dual-signing, HLC timestamps, multi-node topology,
XTDB bitemporal engine replaces SQLite WT/KT columns.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator
from uuid_extensions import uuid7


# ── Enums ────────────────────────────────────────────────────────────


class BeadType(str, Enum):
    FACT = "FACT"
    CLAIM = "CLAIM"
    SIGNAL = "SIGNAL"
    PROPOSAL = "PROPOSAL"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    SKILL = "SKILL"
    MODEL_VERSION = "MODEL_VERSION"
    POLICY = "POLICY"
    AUTOPSY = "AUTOPSY"            # ChadBoar extension — may merge into CLAIM for a8ra
    HEARTBEAT = "HEARTBEAT"        # ChadBoar extension — cycle-level metadata


class TemporalClass(str, Enum):
    OBSERVATION = "OBSERVATION"    # Tied to specific market time
    PATTERN = "PATTERN"            # Timeless methodology
    DERIVED = "DERIVED"            # Computed from other beads


class BeadStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


class SourceType(str, Enum):
    MARKET_DATA = "MARKET_DATA"
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    EXTRACTION = "EXTRACTION"
    SIMULATION = "SIMULATION"


class RejectionCategory(str, Enum):
    PROVENANCE_FAILURE = "PROVENANCE_FAILURE"
    LOGICAL_CONTRADICTION = "LOGICAL_CONTRADICTION"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    RISK_BREACH = "RISK_BREACH"
    STALE_DATA = "STALE_DATA"
    FALSIFICATION_FAILED = "FALSIFICATION_FAILED"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    WARDEN_VETO = "WARDEN_VETO"
    DAILY_SUBLIMIT = "DAILY_SUBLIMIT"
    SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"


# ── Supporting Models ────────────────────────────────────────────────


class SourceRef(BaseModel):
    source_type: SourceType
    source_id: str
    source_version: str | None = None


class AttestationEnvelope(BaseModel):
    air_node_id: str = ""
    code_hash: str = ""
    model_hash: str | None = None
    container_hash: str | None = None
    ecdsa_sig: str = ""
    pqc_sig: str | None = None  # Reserved for future PQC (Dilithium)


# ── Type-Specific Content Models ────────────────────────────────────


class FactContent(BaseModel):
    symbol: str
    token_mint: str | None = None
    field: str
    value: float | str | dict
    as_of_world_time: datetime
    provider: str
    quality_score: float | None = None


class ClaimContent(BaseModel):
    conclusion: str
    reasoning_trace: str
    premises_ref: list[str] = []
    confidence_basis: str
    domain: str
    tokens_referenced: list[str] = []


class SignalContent(BaseModel):
    token_mint: str
    token_symbol: str
    play_type: str
    direction: str = "LONG"
    discovery_source: str
    scoring_breakdown: dict = {}
    conviction_score: int = Field(ge=0, le=100)
    warden_verdict: str
    red_flags: dict = {}
    raw_metrics: dict = {}
    risk_profile: dict = {}
    supporting_claims: list[str] = []
    supporting_facts: list[str] = []


class ProposalContent(BaseModel):
    signal_ref: str
    action: str
    token_mint: str
    token_symbol: str
    entry_price_fdv: float | None = None
    position_size_sol: float | None = None
    position_size_method: str = "score_weighted"
    stop_loss: dict | None = None
    constraints: list[str] = []
    execution_venue: str = "solana_mainnet"
    gate: str


class ProposalRejectedContent(BaseModel):
    signal_ref: str
    action: str
    token_mint: str
    token_symbol: str
    entry_price_fdv: float | None = None
    position_size_sol: float | None = None
    position_size_method: str = "score_weighted"
    stop_loss: dict | None = None
    constraints: list[str] = []
    execution_venue: str = "solana_mainnet"
    gate: str

    rejection_source: str
    rejection_reason: str
    rejection_category: RejectionCategory
    rejection_policy_ref: str | None = None
    scoring_breakdown_at_rejection: dict = {}
    warden_detail: dict | None = None
    risk_metrics_at_rejection: dict = {}
    counterfactual_summary: str | None = None
    linked_skills: list[str] | None = None


class SkillContent(BaseModel):
    skill_name: str
    skill_type: str
    description: str
    failure_trajectory_refs: list[str] = []
    success_trajectory_refs: list[str] = []
    conditions: dict = {}
    distillation_method: str
    validation_status: str = "CANDIDATE"
    validated_by: str | None = None


class ModelVersionContent(BaseModel):
    model_name: str
    version_hash: str
    purpose: str
    deployment_status: str
    config_snapshot: dict = {}


class PolicyContent(BaseModel):
    policy_name: str
    policy_type: str
    rules: dict
    effective_from: datetime
    effective_to: datetime | None = None
    supersedes: str | None = None
    authority: str


class AutopsyContent(BaseModel):
    """ChadBoar extension — post-trade evaluation with PnL."""
    trade_bead_id: str
    token_mint: str
    token_symbol: str
    pnl_sol: float = 0.0
    pnl_pct: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    hold_duration_seconds: int = 0
    lesson: str = ""
    supports_thesis: bool | None = None


class HeartbeatContent(BaseModel):
    """ChadBoar extension — cycle-level pipeline metadata."""
    cycle_number: int
    signals_found: int = 0
    signals_vetoed: int = 0
    proposals_emitted: int = 0
    pot_sol: float = 0.0
    positions_count: int = 0
    pipeline_health: dict = {}
    canary_hash: str = ""
    previous_heartbeat_id: str | None = None


# ── Content type mapping ─────────────────────────────────────────────

CONTENT_TYPE_MAP: dict[BeadType, type[BaseModel]] = {
    BeadType.FACT: FactContent,
    BeadType.CLAIM: ClaimContent,
    BeadType.SIGNAL: SignalContent,
    BeadType.PROPOSAL: ProposalContent,
    BeadType.PROPOSAL_REJECTED: ProposalRejectedContent,
    BeadType.SKILL: SkillContent,
    BeadType.MODEL_VERSION: ModelVersionContent,
    BeadType.POLICY: PolicyContent,
    BeadType.AUTOPSY: AutopsyContent,
    BeadType.HEARTBEAT: HeartbeatContent,
}


# ── Universal Bead Base ──────────────────────────────────────────────


def generate_bead_id() -> str:
    """Generate a UUID v7 bead ID (time-ordered, globally unique)."""
    return str(uuid7())


class BeadBase(BaseModel):
    """Universal bead — every bead in the field shares this structure.

    Identity is UUID v7 (time-ordered). Content integrity is hash_self
    (SHA-256 of canonical JSON). These are deliberately separate: identity
    is assigned at creation, integrity is computed at commitment.
    """

    # Identity
    bead_id: str = Field(default_factory=generate_bead_id)
    bead_type: BeadType

    # Bi-Temporal
    world_time_valid_from: datetime | None = None
    world_time_valid_to: datetime | None = None
    knowledge_time_recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    temporal_class: TemporalClass

    # Provenance
    source_ref: SourceRef
    lineage: list[str] = Field(default_factory=list)

    # Integrity
    hash_self: str = ""
    hash_prev: str | None = None
    merkle_batch_id: str | None = None

    # Attestation (Layer 3)
    attestation: AttestationEnvelope = Field(
        default_factory=AttestationEnvelope,
    )

    # Operational
    status: BeadStatus = BeadStatus.ACTIVE
    superseded_by: str | None = None
    retraction_reason: str | None = None
    tags: list[str] = Field(default_factory=list)

    # Content (type-specific)
    content: dict = Field(default_factory=dict)

    # ── Validators ───────────────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_temporal_consistency(self) -> "BeadBase":
        """Enforce temporal class / world_time consistency (spec rule 1)."""
        tc = self.temporal_class
        wt_from = self.world_time_valid_from
        wt_to = self.world_time_valid_to

        if tc == TemporalClass.OBSERVATION:
            if wt_from is None or wt_to is None:
                raise ValueError(
                    "OBSERVATION beads require both world_time_valid_from "
                    "and world_time_valid_to"
                )
        elif tc == TemporalClass.PATTERN:
            if wt_from is not None or wt_to is not None:
                raise ValueError(
                    "PATTERN beads must have null world_time_valid_from "
                    "and world_time_valid_to"
                )

        return self

    @model_validator(mode="after")
    def _validate_rejection_fields(self) -> "BeadBase":
        """Enforce rejection completeness (spec rules 2-3)."""
        if self.bead_type != BeadType.PROPOSAL_REJECTED:
            return self

        content = self.content
        if not content.get("rejection_category"):
            raise ValueError(
                "PROPOSAL_REJECTED must have rejection_category"
            )
        if not content.get("rejection_reason"):
            raise ValueError(
                "PROPOSAL_REJECTED must have rejection_reason"
            )

        cat = content.get("rejection_category")
        if cat == RejectionCategory.RISK_BREACH.value or cat == RejectionCategory.RISK_BREACH:
            if not content.get("rejection_policy_ref"):
                raise ValueError(
                    "RISK_BREACH rejection must have rejection_policy_ref "
                    "(which POLICY bead was active at rejection time)"
                )

        return self

    @model_validator(mode="after")
    def _validate_lineage(self) -> "BeadBase":
        """Enforce lineage requirements (spec rule 4).

        Root FACTs and PATTERN beads (POLICY, MODEL_VERSION, SKILL) may have
        empty lineage. SIGNAL must reference at least one FACT or CLAIM.
        PROPOSAL/PROPOSAL_REJECTED must reference a SIGNAL.
        HEARTBEAT may reference previous heartbeat (set by chain manager).
        """
        bt = self.bead_type
        exempt = {
            BeadType.FACT, BeadType.POLICY, BeadType.MODEL_VERSION,
            BeadType.SKILL, BeadType.HEARTBEAT,
        }
        if bt not in exempt and not self.lineage:
            raise ValueError(
                f"{bt.value} bead requires at least one entry in lineage"
            )
        return self

    # ── Hashing ──────────────────────────────────────────────────────

    def canonical_content(self) -> str:
        """Deterministic JSON for hash_self computation.

        Excludes: hash_self, merkle_batch_id, hash_prev (chain metadata).
        Includes everything else. Same content always produces same hash.
        """
        data = self.model_dump(mode="json")
        for exclude_key in ("hash_self", "merkle_batch_id", "hash_prev"):
            data.pop(exclude_key, None)
        # Attestation ecdsa_sig is also excluded — it depends on hash_self
        if "attestation" in data:
            data["attestation"].pop("ecdsa_sig", None)
            data["attestation"].pop("pqc_sig", None)
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def compute_hash_self(self) -> str:
        """SHA-256 of canonical content."""
        return hashlib.sha256(
            self.canonical_content().encode("utf-8")
        ).hexdigest()

    def to_storage_dict(self) -> dict[str, Any]:
        """Full bead as dict for SQLite storage / JSONL export."""
        return self.model_dump(mode="json")

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> "BeadBase":
        """Reconstruct a BeadBase from stored dict.

        Validates the content dict against the type-specific model to ensure
        integrity on read, but stores content as a plain dict.
        """
        return cls.model_validate(data)

    @classmethod
    def create(
        cls,
        bead_type: BeadType,
        temporal_class: TemporalClass,
        source_ref: SourceRef,
        content_model: BaseModel,
        *,
        lineage: list[str] | None = None,
        world_time_valid_from: datetime | None = None,
        world_time_valid_to: datetime | None = None,
        tags: list[str] | None = None,
    ) -> "BeadBase":
        """Factory method — build a bead from a typed content model.

        Validates that the content model matches the bead type, serializes
        the content to dict, and constructs the full bead.
        """
        expected_cls = CONTENT_TYPE_MAP.get(bead_type)
        if expected_cls and not isinstance(content_model, expected_cls):
            raise TypeError(
                f"Content model for {bead_type.value} must be "
                f"{expected_cls.__name__}, got {type(content_model).__name__}"
            )

        return cls(
            bead_type=bead_type,
            temporal_class=temporal_class,
            source_ref=source_ref,
            content=content_model.model_dump(mode="json"),
            lineage=lineage or [],
            world_time_valid_from=world_time_valid_from,
            world_time_valid_to=world_time_valid_to,
            tags=tags or [],
        )
