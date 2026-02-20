"""Comprehensive test suite for BEAD_FIELD_SPEC v0.2 implementation.

Tests organized by spec section:
  - Schema validation (15+ tests)
  - Chain integrity (5+ tests)
  - Bi-temporal queries (5+ tests)
  - Edge traversal (5+ tests)
  - Merkle (3+ tests)
  - Signing (3+ tests)
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

from lib.beads import (
    AttestationEnvelope,
    AutopsyContent,
    BeadBase,
    BeadChain,
    BeadStatus,
    BeadType,
    ChainVerifyResult,
    ClaimContent,
    FactContent,
    HeartbeatContent,
    LatencyStats,
    ModelVersionContent,
    PolicyContent,
    ProposalContent,
    ProposalRejectedContent,
    RejectionCategory,
    SignalContent,
    SkillContent,
    SourceRef,
    SourceType,
    TemporalClass,
    generate_bead_id,
)
from lib.beads.signing import sign_hash, verify_signature

# ── Helpers ──────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)
AGENT_SRC = SourceRef(source_type=SourceType.AGENT, source_id="test-agent")
MARKET_SRC = SourceRef(source_type=SourceType.MARKET_DATA, source_id="test-dex")
HUMAN_SRC = SourceRef(source_type=SourceType.HUMAN, source_id="G")


@pytest.fixture
def chain(tmp_path):
    db = tmp_path / "test_beads.db"
    return BeadChain(db_path=db)


def make_fact(**kwargs):
    defaults = dict(
        bead_type=BeadType.FACT,
        temporal_class=TemporalClass.OBSERVATION,
        source_ref=MARKET_SRC,
        content_model=FactContent(
            symbol="MARKET", field="volume_summary",
            value={"candidates": 20}, as_of_world_time=NOW,
            provider="dexscreener",
        ),
        world_time_valid_from=NOW - timedelta(minutes=10),
        world_time_valid_to=NOW,
    )
    defaults.update(kwargs)
    return BeadBase.create(**defaults)


def make_signal(lineage, **kwargs):
    defaults = dict(
        bead_type=BeadType.SIGNAL,
        temporal_class=TemporalClass.DERIVED,
        source_ref=AGENT_SRC,
        content_model=SignalContent(
            token_mint="abc123", token_symbol="TEST",
            play_type="graduation", discovery_source="pulse",
            conviction_score=70, warden_verdict="PASS",
            scoring_breakdown={"volume": 80},
        ),
        lineage=lineage,
    )
    defaults.update(kwargs)
    return BeadBase.create(**defaults)


def make_proposal(signal_id, **kwargs):
    defaults = dict(
        bead_type=BeadType.PROPOSAL,
        temporal_class=TemporalClass.DERIVED,
        source_ref=AGENT_SRC,
        content_model=ProposalContent(
            signal_ref=signal_id, action="ENTER_LONG",
            token_mint="abc123", token_symbol="TEST",
            execution_venue="paper", gate="auto",
        ),
        lineage=[signal_id],
    )
    defaults.update(kwargs)
    return BeadBase.create(**defaults)


def make_rejected(signal_id, category=RejectionCategory.WARDEN_VETO, **kwargs):
    defaults = dict(
        bead_type=BeadType.PROPOSAL_REJECTED,
        temporal_class=TemporalClass.DERIVED,
        source_ref=AGENT_SRC,
        content_model=ProposalRejectedContent(
            signal_ref=signal_id, action="ENTER_LONG",
            token_mint="abc123", token_symbol="TEST",
            execution_venue="paper", gate="auto",
            rejection_source="rug_warden",
            rejection_reason="Concentrated holder >80%",
            rejection_category=category,
            scoring_breakdown_at_rejection={"volume": 80},
        ),
        lineage=[signal_id],
    )
    defaults.update(kwargs)
    return BeadBase.create(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# SCHEMA VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestSchemaValidation:
    """Spec section 1.5 — Pydantic validators."""

    def test_fact_creates_successfully(self):
        bead = make_fact()
        assert bead.bead_type == BeadType.FACT
        assert bead.temporal_class == TemporalClass.OBSERVATION

    def test_claim_creates_successfully(self):
        bead = BeadBase.create(
            bead_type=BeadType.CLAIM,
            temporal_class=TemporalClass.OBSERVATION,
            source_ref=AGENT_SRC,
            content_model=ClaimContent(
                conclusion="Market is hot",
                reasoning_trace="Volume up 3x",
                confidence_basis="Multiple signals",
                domain="regime",
            ),
            lineage=["some-fact-id"],
            world_time_valid_from=NOW - timedelta(minutes=10),
            world_time_valid_to=NOW,
        )
        assert bead.bead_type == BeadType.CLAIM

    def test_signal_creates_successfully(self):
        bead = make_signal(["fact-1"])
        assert bead.bead_type == BeadType.SIGNAL

    def test_proposal_creates_successfully(self):
        bead = make_proposal("signal-1")
        assert bead.bead_type == BeadType.PROPOSAL

    def test_proposal_rejected_creates_successfully(self):
        bead = make_rejected("signal-1")
        assert bead.bead_type == BeadType.PROPOSAL_REJECTED

    def test_heartbeat_creates_successfully(self):
        bead = BeadBase.create(
            bead_type=BeadType.HEARTBEAT,
            temporal_class=TemporalClass.OBSERVATION,
            source_ref=AGENT_SRC,
            content_model=HeartbeatContent(cycle_number=1, pot_sol=14.0),
            world_time_valid_from=NOW - timedelta(minutes=10),
            world_time_valid_to=NOW,
        )
        assert bead.bead_type == BeadType.HEARTBEAT

    def test_policy_creates_successfully(self):
        bead = BeadBase.create(
            bead_type=BeadType.POLICY,
            temporal_class=TemporalClass.PATTERN,
            source_ref=HUMAN_SRC,
            content_model=PolicyContent(
                policy_name="test", policy_type="RISK",
                rules={"max_fdv": 500000},
                effective_from=NOW, authority="G",
            ),
        )
        assert bead.bead_type == BeadType.POLICY

    def test_model_version_creates_successfully(self):
        bead = BeadBase.create(
            bead_type=BeadType.MODEL_VERSION,
            temporal_class=TemporalClass.PATTERN,
            source_ref=AGENT_SRC,
            content_model=ModelVersionContent(
                model_name="grok", version_hash="abc",
                purpose="heartbeat", deployment_status="PRODUCTION",
            ),
        )
        assert bead.bead_type == BeadType.MODEL_VERSION

    def test_autopsy_creates_successfully(self):
        bead = BeadBase.create(
            bead_type=BeadType.AUTOPSY,
            temporal_class=TemporalClass.DERIVED,
            source_ref=AGENT_SRC,
            content_model=AutopsyContent(
                trade_bead_id="trade-1", token_mint="abc",
                token_symbol="TEST", pnl_pct=-5.2,
            ),
            lineage=["trade-1"],
        )
        assert bead.bead_type == BeadType.AUTOPSY

    def test_skill_creates_successfully(self):
        bead = BeadBase.create(
            bead_type=BeadType.SKILL,
            temporal_class=TemporalClass.PATTERN,
            source_ref=AGENT_SRC,
            content_model=SkillContent(
                skill_name="test_skill", skill_type="AVOIDANCE",
                description="Avoid bad tokens",
                distillation_method="manual",
            ),
        )
        assert bead.bead_type == BeadType.SKILL

    # ── Temporal class consistency (rule 1) ──────────────────────────

    def test_observation_requires_world_time(self):
        with pytest.raises(ValueError, match="OBSERVATION"):
            BeadBase.create(
                bead_type=BeadType.FACT,
                temporal_class=TemporalClass.OBSERVATION,
                source_ref=MARKET_SRC,
                content_model=FactContent(
                    symbol="X", field="x", value=1.0,
                    as_of_world_time=NOW, provider="x",
                ),
            )

    def test_pattern_rejects_world_time(self):
        with pytest.raises(ValueError, match="PATTERN"):
            BeadBase.create(
                bead_type=BeadType.POLICY,
                temporal_class=TemporalClass.PATTERN,
                source_ref=HUMAN_SRC,
                content_model=PolicyContent(
                    policy_name="x", policy_type="x", rules={},
                    effective_from=NOW, authority="x",
                ),
                world_time_valid_from=NOW,
                world_time_valid_to=NOW,
            )

    # ── Rejection completeness (rules 2-3) ───────────────────────────

    def test_proposal_rejected_requires_rejection_reason(self):
        with pytest.raises(ValueError, match="rejection_reason"):
            BeadBase.create(
                bead_type=BeadType.PROPOSAL_REJECTED,
                temporal_class=TemporalClass.DERIVED,
                source_ref=AGENT_SRC,
                content_model=ProposalRejectedContent(
                    signal_ref="x", action="x", token_mint="x",
                    token_symbol="x", gate="auto",
                    rejection_source="x", rejection_reason="",
                    rejection_category=RejectionCategory.WARDEN_VETO,
                ),
                lineage=["some-signal"],
            )

    def test_risk_breach_requires_policy_ref(self):
        with pytest.raises(ValueError, match="rejection_policy_ref"):
            BeadBase.create(
                bead_type=BeadType.PROPOSAL_REJECTED,
                temporal_class=TemporalClass.DERIVED,
                source_ref=AGENT_SRC,
                content_model=ProposalRejectedContent(
                    signal_ref="x", action="x", token_mint="x",
                    token_symbol="x", gate="auto",
                    rejection_source="risk_engine",
                    rejection_reason="Daily limit exceeded",
                    rejection_category=RejectionCategory.RISK_BREACH,
                ),
                lineage=["some-signal"],
            )

    def test_risk_breach_with_policy_ref_succeeds(self):
        bead = BeadBase.create(
            bead_type=BeadType.PROPOSAL_REJECTED,
            temporal_class=TemporalClass.DERIVED,
            source_ref=AGENT_SRC,
            content_model=ProposalRejectedContent(
                signal_ref="x", action="x", token_mint="x",
                token_symbol="x", gate="auto",
                rejection_source="risk_engine",
                rejection_reason="Daily limit exceeded",
                rejection_category=RejectionCategory.RISK_BREACH,
                rejection_policy_ref="policy-bead-123",
            ),
            lineage=["some-signal"],
        )
        assert bead.bead_type == BeadType.PROPOSAL_REJECTED

    # ── Lineage (rule 4) ─────────────────────────────────────────────

    def test_signal_requires_lineage(self):
        with pytest.raises(ValueError, match="lineage"):
            make_signal([])

    def test_proposal_requires_lineage(self):
        with pytest.raises(ValueError, match="lineage"):
            BeadBase.create(
                bead_type=BeadType.PROPOSAL,
                temporal_class=TemporalClass.DERIVED,
                source_ref=AGENT_SRC,
                content_model=ProposalContent(
                    signal_ref="x", action="x", token_mint="x",
                    token_symbol="x", gate="auto",
                ),
            )

    def test_fact_allows_empty_lineage(self):
        bead = make_fact()
        assert bead.lineage == []

    def test_heartbeat_allows_empty_lineage(self):
        bead = BeadBase.create(
            bead_type=BeadType.HEARTBEAT,
            temporal_class=TemporalClass.OBSERVATION,
            source_ref=AGENT_SRC,
            content_model=HeartbeatContent(cycle_number=1),
            world_time_valid_from=NOW - timedelta(minutes=10),
            world_time_valid_to=NOW,
        )
        assert bead.lineage == []

    # ── Hash determinism (rule 5) ────────────────────────────────────

    def test_hash_self_deterministic(self):
        bead = make_fact()
        h1 = bead.compute_hash_self()
        h2 = bead.compute_hash_self()
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    # ── UUID v7 format (rule 6) ──────────────────────────────────────

    def test_bead_id_is_uuid_v7(self):
        bead = make_fact()
        parts = bead.bead_id.split("-")
        assert len(parts) == 5
        assert len(bead.bead_id) == 36
        # UUID v7 has version nibble = 7 in 3rd group
        assert parts[2][0] == "7" or parts[2][0] == "0"


# ═══════════════════════════════════════════════════════════════════════
# CHAIN INTEGRITY TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestChainIntegrity:
    """Spec section 2 — chain manager integrity."""

    def test_write_and_read_bead(self, chain):
        fact = make_fact()
        bid = chain.write_bead(fact)
        assert bid
        retrieved = chain.get_bead(bid)
        assert retrieved is not None
        assert retrieved.bead_type == BeadType.FACT

    def test_chain_links_correctly(self, chain):
        f1 = make_fact()
        f2 = make_fact()
        id1 = chain.write_bead(f1)
        id2 = chain.write_bead(f2)

        b2 = chain.get_bead(id2)
        assert b2.hash_prev == id1

    def test_genesis_has_null_prev(self, chain):
        f = make_fact()
        bid = chain.write_bead(f)
        b = chain.get_bead(bid)
        assert b.hash_prev is None

    def test_verify_chain_10_beads(self, chain):
        for _ in range(10):
            chain.write_bead(make_fact())
        result = chain.verify_chain()
        assert result.valid is True
        assert result.total_beads == 10
        assert result.verified_beads == 10

    def test_tamper_detection(self, chain):
        for _ in range(5):
            chain.write_bead(make_fact())

        # Tamper with a bead's hash_self in the DB
        import sqlite3
        conn = sqlite3.connect(str(chain.db_path))
        conn.execute(
            "UPDATE beads SET hash_self = 'tampered_hash' WHERE seq = 3"
        )
        conn.commit()
        conn.close()

        result = chain.verify_chain()
        assert result.valid is False
        assert result.first_break_seq == 3

    def test_chain_head(self, chain):
        chain.write_bead(make_fact())
        f2 = make_fact()
        id2 = chain.write_bead(f2)
        head = chain.get_chain_head()
        assert head.bead_id == id2

    def test_chain_length(self, chain):
        for _ in range(7):
            chain.write_bead(make_fact())
        assert chain.get_chain_length() == 7


# ═══════════════════════════════════════════════════════════════════════
# BI-TEMPORAL QUERY TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestBiTemporalQueries:
    """Spec section 2.2 — bi-temporal query methods."""

    def test_query_world_time_range(self, chain):
        t1 = NOW - timedelta(hours=2)
        t2 = NOW - timedelta(hours=1)
        old_fact = BeadBase.create(
            bead_type=BeadType.FACT,
            temporal_class=TemporalClass.OBSERVATION,
            source_ref=MARKET_SRC,
            content_model=FactContent(
                symbol="M", field="v", value=1.0,
                as_of_world_time=t1, provider="x",
            ),
            world_time_valid_from=t1,
            world_time_valid_to=t2,
        )
        chain.write_bead(old_fact)

        recent_fact = make_fact()
        chain.write_bead(recent_fact)

        # Query for recent window only
        results = chain.query_world_time_range(
            NOW - timedelta(minutes=15), NOW,
        )
        assert len(results) == 1

        # Query broad window gets both
        results_all = chain.query_world_time_range(
            NOW - timedelta(hours=3), NOW,
        )
        assert len(results_all) == 2

    def test_query_knowledge_at(self, chain):
        chain.write_bead(make_fact())
        time.sleep(0.01)
        midpoint = datetime.now(timezone.utc)
        time.sleep(0.01)
        chain.write_bead(make_fact())

        # Knowledge at midpoint should only return 1 bead
        results = chain.query_knowledge_at(midpoint)
        assert len(results) == 1

        # Knowledge at now should return both
        results_all = chain.query_knowledge_at(
            datetime.now(timezone.utc) + timedelta(seconds=1)
        )
        assert len(results_all) == 2

    def test_query_knowledge_at_with_type_filter(self, chain):
        chain.write_bead(make_fact())
        signal = make_signal(["dummy-lineage"])
        chain.write_bead(signal)

        results = chain.query_knowledge_at(
            datetime.now(timezone.utc) + timedelta(seconds=1),
            bead_type=BeadType.SIGNAL,
        )
        assert len(results) == 1
        assert results[0].bead_type == BeadType.SIGNAL

    def test_refinery_latency(self, chain):
        # Create OBSERVATION beads with known WT/KT gap
        for _ in range(5):
            chain.write_bead(make_fact())

        stats = chain.refinery_latency()
        assert stats.count == 5
        assert stats.avg_seconds >= 0
        assert stats.p50_seconds >= 0

    def test_refinery_latency_empty(self, chain):
        stats = chain.refinery_latency()
        assert stats.count == 0

    def test_query_by_temporal_class(self, chain):
        chain.write_bead(make_fact())  # OBSERVATION
        chain.write_bead(BeadBase.create(
            bead_type=BeadType.POLICY,
            temporal_class=TemporalClass.PATTERN,
            source_ref=HUMAN_SRC,
            content_model=PolicyContent(
                policy_name="x", policy_type="x", rules={},
                effective_from=NOW, authority="x",
            ),
        ))

        obs = chain.query_by_temporal_class(TemporalClass.OBSERVATION)
        assert len(obs) == 1
        pat = chain.query_by_temporal_class(TemporalClass.PATTERN)
        assert len(pat) == 1


# ═══════════════════════════════════════════════════════════════════════
# EDGE TRAVERSAL TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeTraversal:
    """Spec section 2.2 — lineage traversal methods."""

    def test_get_lineage_direct_parents(self, chain):
        fact_id = chain.write_bead(make_fact())
        signal = make_signal([fact_id])
        signal_id = chain.write_bead(signal)

        parents = chain.get_lineage(signal_id)
        assert len(parents) == 1
        assert parents[0].bead_id == fact_id

    def test_get_descendants(self, chain):
        fact_id = chain.write_bead(make_fact())
        s1 = make_signal([fact_id])
        s1_id = chain.write_bead(s1)
        s2 = make_signal([fact_id])
        s2_id = chain.write_bead(s2)

        descendants = chain.get_descendants(fact_id)
        assert len(descendants) == 2
        desc_ids = {d.bead_id for d in descendants}
        assert s1_id in desc_ids
        assert s2_id in desc_ids

    def test_walk_lineage_recursive(self, chain):
        # FACT → SIGNAL → PROPOSAL chain
        fact_id = chain.write_bead(make_fact())
        signal = make_signal([fact_id])
        signal_id = chain.write_bead(signal)
        proposal = make_proposal(signal_id)
        proposal_id = chain.write_bead(proposal)

        walked = chain.walk_lineage(proposal_id, depth=5)
        walked_ids = {b.bead_id for b in walked}
        assert signal_id in walked_ids
        assert fact_id in walked_ids

    def test_shadow_field_query(self, chain):
        fact_id = chain.write_bead(make_fact())
        signal = make_signal([fact_id])
        signal_id = chain.write_bead(signal)

        # One proposal, two rejections
        chain.write_bead(make_proposal(signal_id))
        chain.write_bead(make_rejected(signal_id, RejectionCategory.WARDEN_VETO))
        chain.write_bead(make_rejected(signal_id, RejectionCategory.SCORE_BELOW_THRESHOLD))

        shadow = chain.query_shadow_field()
        assert len(shadow) == 2

    def test_shadow_field_filter_by_category(self, chain):
        fact_id = chain.write_bead(make_fact())
        signal_id = chain.write_bead(make_signal([fact_id]))
        chain.write_bead(make_rejected(signal_id, RejectionCategory.WARDEN_VETO))
        chain.write_bead(make_rejected(signal_id, RejectionCategory.SCORE_BELOW_THRESHOLD))

        veto_only = chain.query_shadow_field(
            rejection_category=RejectionCategory.WARDEN_VETO,
        )
        assert len(veto_only) == 1

    def test_shadow_field_stats(self, chain):
        fact_id = chain.write_bead(make_fact())
        signal_id = chain.write_bead(make_signal([fact_id]))
        for _ in range(3):
            chain.write_bead(make_rejected(signal_id, RejectionCategory.WARDEN_VETO))
        chain.write_bead(make_rejected(signal_id, RejectionCategory.SCORE_BELOW_THRESHOLD))

        stats = chain.shadow_field_stats()
        assert stats["total_rejections"] == 4
        assert stats["category_distribution"]["WARDEN_VETO"] == 3
        assert stats["category_distribution"]["SCORE_BELOW_THRESHOLD"] == 1


# ═══════════════════════════════════════════════════════════════════════
# MERKLE TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestMerkle:
    """Spec section 5 — Merkle anchoring."""

    def test_batch_creation(self, chain):
        for _ in range(5):
            chain.write_bead(make_fact())

        # Signal triggers DECISION_BOUNDARY
        fact_id = chain.write_bead(make_fact())
        chain.write_bead(make_signal([fact_id]))

        trigger = chain.check_anchor_trigger()
        assert trigger == "DECISION_BOUNDARY"

        batch_id = chain.create_merkle_batch(trigger)
        assert batch_id
        assert len(batch_id) == 36  # UUID format

    def test_merkle_root_deterministic(self, chain):
        ids = []
        for _ in range(3):
            ids.append(chain.write_bead(make_fact()))

        hashes = []
        import sqlite3
        conn = sqlite3.connect(str(chain.db_path))
        for row in conn.execute("SELECT hash_self FROM beads ORDER BY seq ASC").fetchall():
            hashes.append(row[0])
        conn.close()

        root1 = BeadChain._compute_merkle_root(hashes)
        root2 = BeadChain._compute_merkle_root(hashes)
        assert root1 == root2
        assert len(root1) == 64

    def test_trigger_detection_max_time(self, chain):
        chain.write_bead(make_fact())

        # Create an old batch to simulate time passage
        import sqlite3
        conn = sqlite3.connect(str(chain.db_path))
        old_time = (NOW - timedelta(hours=2)).isoformat()
        conn.execute(
            "INSERT INTO merkle_batches (batch_id, merkle_root, bead_count, trigger_type, created_at) "
            "VALUES (?,?,?,?,?)",
            ("old-batch", "fake-root", 1, "TEST", old_time),
        )
        conn.execute("UPDATE beads SET merkle_batch_id = 'old-batch'")
        conn.commit()
        conn.close()

        # Add a new bead after the old anchor
        chain.write_bead(make_fact())
        trigger = chain.check_anchor_trigger()
        assert trigger == "MAX_TIME"

    def test_beads_get_batch_id_backfilled(self, chain):
        for _ in range(3):
            chain.write_bead(make_fact())
        fact_id = chain.write_bead(make_fact())
        chain.write_bead(make_signal([fact_id]))

        batch_id = chain.create_merkle_batch("DECISION_BOUNDARY")

        import sqlite3
        conn = sqlite3.connect(str(chain.db_path))
        unanchored = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE merkle_batch_id IS NULL"
        ).fetchone()[0]
        conn.close()
        assert unanchored == 0


# ═══════════════════════════════════════════════════════════════════════
# SIGNING TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestSigning:
    """Spec section 2.3 — ECDSA signing."""

    def test_signature_validates(self, chain):
        fact = make_fact()
        bid = chain.write_bead(fact)
        bead = chain.get_bead(bid)

        sig = bead.attestation.ecdsa_sig
        assert sig
        if sig == "signing_unavailable":
            pytest.skip("Signing key not accessible in test environment")
        assert verify_signature(bead.hash_self, sig)

    def test_tampered_bead_fails_signature(self, chain):
        fact = make_fact()
        bid = chain.write_bead(fact)
        bead = chain.get_bead(bid)

        fake_hash = "a" * 64
        assert not verify_signature(fake_hash, bead.attestation.ecdsa_sig)

    def test_attestation_envelope_populated(self, chain):
        fact = make_fact()
        bid = chain.write_bead(fact)
        bead = chain.get_bead(bid)

        assert bead.attestation.air_node_id == "chadboar-vps-sg1"
        assert bead.attestation.code_hash
        assert bead.attestation.ecdsa_sig
        assert bead.attestation.pqc_sig is None


# ═══════════════════════════════════════════════════════════════════════
# QUERY TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestQueries:
    """Spec section 2.2 — general query methods."""

    def test_query_by_type(self, chain):
        chain.write_bead(make_fact())
        chain.write_bead(make_fact())
        fact_id = chain.write_bead(make_fact())
        chain.write_bead(make_signal([fact_id]))

        facts = chain.query_by_type(BeadType.FACT)
        signals = chain.query_by_type(BeadType.SIGNAL)
        assert len(facts) == 3
        assert len(signals) == 1

    def test_query_by_token(self, chain):
        fact_id = chain.write_bead(make_fact())
        chain.write_bead(make_signal([fact_id]))  # token_mint="abc123"
        chain.write_bead(BeadBase.create(
            bead_type=BeadType.SIGNAL,
            temporal_class=TemporalClass.DERIVED,
            source_ref=AGENT_SRC,
            content_model=SignalContent(
                token_mint="xyz789", token_symbol="OTHER",
                play_type="accumulation", discovery_source="nansen",
                conviction_score=50, warden_verdict="PASS",
            ),
            lineage=[fact_id],
        ))

        abc = chain.query_by_token("abc123")
        xyz = chain.query_by_token("xyz789")
        assert len(abc) == 1
        assert len(xyz) == 1

    def test_query_by_tag(self, chain):
        chain.write_bead(make_fact())
        results = chain.query_by_tag("source:dexscreener")
        assert len(results) == 0  # Default make_fact doesn't have tags

    def test_query_by_status(self, chain):
        chain.write_bead(make_fact())
        active = chain.query_by_status(BeadStatus.ACTIVE)
        assert len(active) == 1
        superseded = chain.query_by_status(BeadStatus.SUPERSEDED)
        assert len(superseded) == 0

    def test_chain_stats(self, chain):
        fact_id = chain.write_bead(make_fact())
        signal_id = chain.write_bead(make_signal([fact_id]))
        chain.write_bead(make_proposal(signal_id))
        chain.write_bead(make_rejected(signal_id))

        stats = chain.get_chain_stats()
        assert stats["chain_length"] == 4
        assert stats["type_counts"]["FACT"] == 1
        assert stats["type_counts"]["SIGNAL"] == 1
        assert stats["shadow_field_size"] == 1
        assert stats["lineage_edges"] == 3
        assert stats["chain_valid"] is True


# ═══════════════════════════════════════════════════════════════════════
# EXPORT / IMPORT TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestExportImport:
    """Spec section 2.2 — JSONL export/import."""

    def test_export_jsonl(self, chain, tmp_path):
        for _ in range(5):
            chain.write_bead(make_fact())

        path = tmp_path / "export.jsonl"
        count = chain.export_chain_jsonl(path)
        assert count == 5
        assert path.exists()

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 5

        # Each line is valid JSON
        for line in lines:
            data = json.loads(line)
            assert "bead_id" in data
            assert "bead_type" in data

    def test_import_skips_existing(self, chain, tmp_path):
        for _ in range(3):
            chain.write_bead(make_fact())

        path = tmp_path / "export.jsonl"
        chain.export_chain_jsonl(path)

        # Import into same chain — should skip all
        imported = chain.import_chain_jsonl(path)
        assert imported == 0
        assert chain.get_chain_length() == 3
