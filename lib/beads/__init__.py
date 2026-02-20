"""Bead Field — BEAD_FIELD_SPEC v0.2 implementation for ChadBoar.

The bead chain is ChadBoar's structured memory and the a8ra substrate
running in canary mode. Every signal, proposal, rejection, and reflection
is captured as a UUID v7-identified, ECDSA-signed, bi-temporal bead.

Schema: lib/beads/schema.py (Pydantic v2 strict, 10 bead types)
Chain:  lib/beads/chain.py  (SQLite, lineage table, Merkle batching)
Signing: lib/beads/signing.py (ECDSA secp256r1, node attestation)
"""

from lib.beads.schema import (
    BeadBase,
    BeadType,
    BeadStatus,
    TemporalClass,
    SourceType,
    RejectionCategory,
    SourceRef,
    AttestationEnvelope,
    FactContent,
    ClaimContent,
    SignalContent,
    ProposalContent,
    ProposalRejectedContent,
    SkillContent,
    ModelVersionContent,
    PolicyContent,
    AutopsyContent,
    HeartbeatContent,
    CONTENT_TYPE_MAP,
    generate_bead_id,
)
from lib.beads.chain import BeadChain, ChainVerifyResult, LatencyStats
from lib.beads.signing import (
    sign_hash,
    verify_signature,
    get_code_hash,
    get_public_key_hex,
    NODE_ID,
)

__all__ = [
    # Schema
    "BeadBase",
    "BeadType",
    "BeadStatus",
    "TemporalClass",
    "SourceType",
    "RejectionCategory",
    "SourceRef",
    "AttestationEnvelope",
    # Content models
    "FactContent",
    "ClaimContent",
    "SignalContent",
    "ProposalContent",
    "ProposalRejectedContent",
    "SkillContent",
    "ModelVersionContent",
    "PolicyContent",
    "AutopsyContent",
    "HeartbeatContent",
    "CONTENT_TYPE_MAP",
    "generate_bead_id",
    # Chain
    "BeadChain",
    "ChainVerifyResult",
    "LatencyStats",
    # Signing
    "sign_hash",
    "verify_signature",
    "get_code_hash",
    "get_public_key_hex",
    "NODE_ID",
]
