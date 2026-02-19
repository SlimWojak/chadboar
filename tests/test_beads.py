"""Tests for the bead intelligence substrate — schema + chain manager."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from lib.beads.schema import (
    GENESIS_PREV_HASH,
    Bead,
    BeadEdges,
    BeadHeader,
    BeadProvenance,
    BeadType,
    AutopsyPayload,
    HeartbeatPayload,
    InsightPayload,
    SignalPayload,
    TradePayload,
    VerdictPayload,
)
from lib.beads.chain import BeadChain, ChainVerifyResult


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_chain():
    """Create a BeadChain backed by a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield BeadChain(db_path=Path(tmpdir) / "beads.db")


def _make_signal(mint: str = "So111", symbol: str = "TEST") -> Bead:
    return Bead(
        header=BeadHeader(bead_type=BeadType.SIGNAL),
        payload=SignalPayload(
            token_mint=mint,
            token_symbol=symbol,
            play_type="graduation",
            discovery_source="pulse-bonding",
            raw_metrics={"volume_1h": 50000},
        ),
    )


def _make_verdict(mint: str = "So111", derived: list[str] | None = None) -> Bead:
    return Bead(
        header=BeadHeader(bead_type=BeadType.VERDICT),
        edges=BeadEdges(derived_from=derived or []),
        payload=VerdictPayload(
            token_mint=mint,
            token_symbol="TEST",
            play_type="graduation",
            conviction_score=72,
            recommendation="AUTO_EXECUTE",
            warden_verdict="PASS",
            scoring_breakdown={"pulse_quality": 85},
        ),
    )


def _make_heartbeat(cycle: int) -> Bead:
    return Bead(
        header=BeadHeader(bead_type=BeadType.HEARTBEAT),
        payload=HeartbeatPayload(
            cycle_number=cycle,
            signals_found=10,
            pot_sol=14.0,
            pipeline_health={"dexscreener": "OK"},
        ),
    )


# ── Schema Tests ───────────────────────────────────────────────────────


class TestBeadSchema:
    def test_signal_payload_validates(self):
        bead = _make_signal()
        assert bead.header.bead_type == BeadType.SIGNAL
        assert bead.payload.token_mint == "So111"

    def test_verdict_payload_validates(self):
        bead = _make_verdict(derived=["abc123"])
        assert bead.payload.conviction_score == 72
        assert bead.edges.derived_from == ["abc123"]

    def test_trade_payload_validates(self):
        bead = Bead(
            header=BeadHeader(bead_type=BeadType.TRADE),
            edges=BeadEdges(derived_from=["verdict_id"]),
            payload=TradePayload(
                token_mint="So111",
                token_symbol="TEST",
                play_type="graduation",
                conviction_score=72,
                recommendation="PAPER_TRADE",
                warden_verdict="PASS",
                entry_price=0.00012,
                entry_amount_sol=0.5,
                gate="paper",
            ),
        )
        assert bead.payload.gate == "paper"

    def test_autopsy_payload_validates(self):
        bead = Bead(
            header=BeadHeader(bead_type=BeadType.AUTOPSY),
            edges=BeadEdges(derived_from=["trade_id"], supports=["verdict_id"]),
            payload=AutopsyPayload(
                trade_bead_id="trade_id",
                pnl_sol=0.3,
                pnl_pct=15.0,
                exit_reason="tp_hit",
                lesson="Volume spike confirmed thesis.",
            ),
        )
        assert bead.payload.pnl_sol == 0.3
        # Autopsy auto-appends trade_bead_id to derived_from
        assert "trade_id" in bead.edges.derived_from

    def test_insight_payload_validates(self):
        bead = Bead(
            header=BeadHeader(bead_type=BeadType.INSIGHT),
            edges=BeadEdges(derived_from=["bead1", "bead2"]),
            payload=InsightPayload(
                insight_type="pattern",
                content="Volume spikes >3x baseline correlate with 2x exits.",
                evidence_bead_ids=["bead1", "bead2"],
                confidence=0.8,
            ),
        )
        assert bead.payload.confidence == 0.8

    def test_heartbeat_payload_validates(self):
        bead = _make_heartbeat(220)
        assert bead.payload.cycle_number == 220
        # Heartbeat doesn't require derived_from
        assert bead.edges.edges_complete is True

    def test_timestamp_auto_set(self):
        bead = _make_signal()
        assert bead.header.timestamp != ""

    def test_explicit_timestamp_preserved(self):
        bead = Bead(
            header=BeadHeader(
                bead_type=BeadType.SIGNAL,
                timestamp="2026-01-01T00:00:00+00:00",
            ),
            payload=SignalPayload(
                token_mint="So111",
                token_symbol="TEST",
                play_type="graduation",
                discovery_source="test",
            ),
        )
        assert bead.header.timestamp == "2026-01-01T00:00:00+00:00"


class TestEdgeDiscipline:
    def test_non_heartbeat_without_derived_marks_incomplete(self):
        bead = _make_signal()  # No derived_from
        assert bead.edges.edges_complete is False
        assert "no derived_from" in bead.edges.edges_incomplete_reason

    def test_heartbeat_without_derived_stays_complete(self):
        bead = _make_heartbeat(1)
        assert bead.edges.edges_complete is True

    def test_verdict_with_derived_stays_complete(self):
        bead = _make_verdict(derived=["signal_id"])
        assert bead.edges.edges_complete is True

    def test_autopsy_without_support_or_contradict_marks_incomplete(self):
        bead = Bead(
            header=BeadHeader(bead_type=BeadType.AUTOPSY),
            edges=BeadEdges(derived_from=["trade_id"]),
            payload=AutopsyPayload(trade_bead_id="trade_id"),
        )
        assert bead.edges.edges_complete is False
        assert "support or contradict" in bead.edges.edges_incomplete_reason

    def test_autopsy_with_support_stays_complete(self):
        bead = Bead(
            header=BeadHeader(bead_type=BeadType.AUTOPSY),
            edges=BeadEdges(
                derived_from=["trade_id"],
                supports=["verdict_id"],
            ),
            payload=AutopsyPayload(trade_bead_id="trade_id"),
        )
        assert bead.edges.edges_complete is True

    def test_autopsy_auto_appends_trade_ref(self):
        bead = Bead(
            header=BeadHeader(bead_type=BeadType.AUTOPSY),
            edges=BeadEdges(supports=["v1"]),
            payload=AutopsyPayload(trade_bead_id="trade_123"),
        )
        assert "trade_123" in bead.edges.derived_from


class TestCanonicalContent:
    def test_deterministic_hash(self):
        b1 = _make_signal()
        b2 = Bead(
            header=BeadHeader(
                bead_type=BeadType.SIGNAL,
                timestamp=b1.header.timestamp,
            ),
            payload=SignalPayload(
                token_mint="So111",
                token_symbol="TEST",
                play_type="graduation",
                discovery_source="pulse-bonding",
                raw_metrics={"volume_1h": 50000},
            ),
        )
        assert b1.compute_bead_id() == b2.compute_bead_id()

    def test_different_content_different_hash(self):
        b1 = _make_signal(symbol="AAA")
        b2 = _make_signal(symbol="BBB")
        assert b1.compute_bead_id() != b2.compute_bead_id()

    def test_bead_id_is_64_char_hex(self):
        bead = _make_signal()
        bead_id = bead.compute_bead_id()
        assert len(bead_id) == 64
        int(bead_id, 16)  # valid hex

    def test_prev_hash_excluded_from_content(self):
        """Two beads with different prev_hash but same content produce same hash."""
        b1 = _make_signal()
        b1.header.prev_hash = "a" * 64
        b2 = Bead(
            header=BeadHeader(
                bead_type=BeadType.SIGNAL,
                timestamp=b1.header.timestamp,
                prev_hash="b" * 64,
            ),
            payload=SignalPayload(
                token_mint="So111",
                token_symbol="TEST",
                play_type="graduation",
                discovery_source="pulse-bonding",
                raw_metrics={"volume_1h": 50000},
            ),
        )
        assert b1.compute_bead_id() == b2.compute_bead_id()


class TestSerialization:
    def test_round_trip(self):
        bead = _make_verdict(derived=["sig1", "sig2"])
        chain_dict = bead.to_chain_dict()
        restored = Bead.from_chain_dict(chain_dict)
        assert restored.header.bead_type == BeadType.VERDICT
        assert restored.payload.conviction_score == 72
        assert restored.edges.derived_from == ["sig1", "sig2"]

    def test_round_trip_all_types(self):
        beads = [
            _make_signal(),
            _make_verdict(derived=["x"]),
            _make_heartbeat(1),
            Bead(
                header=BeadHeader(bead_type=BeadType.TRADE),
                edges=BeadEdges(derived_from=["v1"]),
                payload=TradePayload(
                    token_mint="So111", token_symbol="T", play_type="graduation",
                    conviction_score=50, recommendation="PAPER_TRADE",
                    warden_verdict="PASS", entry_price=0.001,
                    entry_amount_sol=0.5, gate="paper",
                ),
            ),
            Bead(
                header=BeadHeader(bead_type=BeadType.AUTOPSY),
                edges=BeadEdges(derived_from=["t1"], supports=["v1"]),
                payload=AutopsyPayload(trade_bead_id="t1", pnl_pct=10.0),
            ),
            Bead(
                header=BeadHeader(bead_type=BeadType.INSIGHT),
                edges=BeadEdges(derived_from=["a1"]),
                payload=InsightPayload(
                    insight_type="pattern",
                    content="test pattern",
                    confidence=0.9,
                ),
            ),
        ]
        for bead in beads:
            d = bead.to_chain_dict()
            restored = Bead.from_chain_dict(d)
            assert restored.header.bead_type == bead.header.bead_type


# ── Chain Manager Tests ────────────────────────────────────────────────


class TestChainWrite:
    def test_genesis_bead(self, tmp_chain):
        bead = _make_signal()
        bead_id = tmp_chain.write_bead(bead)
        assert len(bead_id) == 64
        assert bead.header.prev_hash == GENESIS_PREV_HASH
        assert tmp_chain.get_chain_length() == 1

    def test_chain_linkage(self, tmp_chain):
        b1 = _make_signal(symbol="A")
        id1 = tmp_chain.write_bead(b1)

        b2 = _make_signal(symbol="B")
        id2 = tmp_chain.write_bead(b2)

        assert b2.header.prev_hash == id1
        assert id1 != id2

    def test_chain_linkage_three(self, tmp_chain):
        ids = []
        for sym in ["A", "B", "C"]:
            bead = _make_signal(symbol=sym)
            ids.append(tmp_chain.write_bead(bead))

        b2 = tmp_chain.get_bead(ids[1])
        b3 = tmp_chain.get_bead(ids[2])
        assert b2.header.prev_hash == ids[0]
        assert b3.header.prev_hash == ids[1]


class TestChainRead:
    def test_get_bead(self, tmp_chain):
        bead = _make_signal()
        bead_id = tmp_chain.write_bead(bead)
        retrieved = tmp_chain.get_bead(bead_id)
        assert retrieved is not None
        assert retrieved.header.bead_id == bead_id
        assert retrieved.payload.token_mint == "So111"

    def test_get_bead_missing(self, tmp_chain):
        assert tmp_chain.get_bead("nonexistent") is None

    def test_get_chain_head(self, tmp_chain):
        assert tmp_chain.get_chain_head() is None

        bead = _make_signal()
        bead_id = tmp_chain.write_bead(bead)
        head = tmp_chain.get_chain_head()
        assert head is not None
        assert head.header.bead_id == bead_id

    def test_get_chain_head_is_latest(self, tmp_chain):
        tmp_chain.write_bead(_make_signal(symbol="OLD"))
        tmp_chain.write_bead(_make_signal(symbol="NEW"))
        head = tmp_chain.get_chain_head()
        assert head.payload.token_symbol == "NEW"


class TestChainQuery:
    def test_query_by_type(self, tmp_chain):
        tmp_chain.write_bead(_make_signal())
        tmp_chain.write_bead(_make_signal())
        tmp_chain.write_bead(_make_heartbeat(1))

        signals = tmp_chain.query_by_type(BeadType.SIGNAL)
        assert len(signals) == 2
        heartbeats = tmp_chain.query_by_type(BeadType.HEARTBEAT)
        assert len(heartbeats) == 1

    def test_query_by_type_string(self, tmp_chain):
        tmp_chain.write_bead(_make_signal())
        signals = tmp_chain.query_by_type("signal")
        assert len(signals) == 1

    def test_query_by_type_limit(self, tmp_chain):
        for i in range(10):
            tmp_chain.write_bead(_make_signal(symbol=f"T{i}"))
        signals = tmp_chain.query_by_type(BeadType.SIGNAL, limit=3)
        assert len(signals) == 3

    def test_query_by_token(self, tmp_chain):
        tmp_chain.write_bead(_make_signal(mint="MINT_A"))
        tmp_chain.write_bead(_make_signal(mint="MINT_B"))
        tmp_chain.write_bead(_make_signal(mint="MINT_A"))

        results = tmp_chain.query_by_token("MINT_A")
        assert len(results) == 2

    def test_query_by_edge(self, tmp_chain):
        sig_id = tmp_chain.write_bead(_make_signal())
        verdict = _make_verdict(derived=[sig_id])
        tmp_chain.write_bead(verdict)

        linked = tmp_chain.query_by_edge(sig_id)
        assert len(linked) == 1
        assert linked[0].header.bead_type == BeadType.VERDICT

    def test_query_by_edge_multiple(self, tmp_chain):
        sig_id = tmp_chain.write_bead(_make_signal())
        v1 = _make_verdict(derived=[sig_id])
        tmp_chain.write_bead(v1)
        v2 = _make_verdict(derived=[sig_id])
        tmp_chain.write_bead(v2)
        tmp_chain.write_bead(_make_heartbeat(1))  # unrelated

        linked = tmp_chain.query_by_edge(sig_id)
        assert len(linked) == 2


class TestChainVerify:
    def test_empty_chain_valid(self, tmp_chain):
        result = tmp_chain.verify_chain()
        assert result.valid is True
        assert result.total_beads == 0

    def test_valid_chain(self, tmp_chain):
        for i in range(5):
            tmp_chain.write_bead(_make_heartbeat(i))
        result = tmp_chain.verify_chain()
        assert result.valid is True
        assert result.verified_beads == 5
        assert "integrity OK" in result.message

    def test_tampered_hash_detected(self, tmp_chain):
        for i in range(5):
            tmp_chain.write_bead(_make_heartbeat(i))

        # Tamper with bead 3's stored hash
        conn = sqlite3.connect(tmp_chain.db_path)
        conn.execute(
            "UPDATE beads SET bead_id = ? WHERE seq = 3", ("f" * 64,)
        )
        conn.commit()
        conn.close()

        result = tmp_chain.verify_chain()
        assert result.valid is False
        assert "Hash mismatch" in result.message

    def test_tampered_payload_detected(self, tmp_chain):
        for i in range(5):
            tmp_chain.write_bead(_make_heartbeat(i))

        # Tamper with the full_bead JSON (change cycle_number)
        conn = sqlite3.connect(tmp_chain.db_path)
        row = conn.execute(
            "SELECT full_bead FROM beads WHERE seq = 3"
        ).fetchone()
        data = json.loads(row[0])
        data["payload"]["cycle_number"] = 999
        conn.execute(
            "UPDATE beads SET full_bead = ? WHERE seq = 3",
            (json.dumps(data, sort_keys=True),),
        )
        conn.commit()
        conn.close()

        result = tmp_chain.verify_chain()
        assert result.valid is False
        assert "Hash mismatch" in result.message

    def test_broken_prev_link_detected(self, tmp_chain):
        for i in range(5):
            tmp_chain.write_bead(_make_heartbeat(i))

        # Break prev_hash at seq 4
        conn = sqlite3.connect(tmp_chain.db_path)
        conn.execute(
            "UPDATE beads SET prev_hash = ? WHERE seq = 4", ("0" * 64,)
        )
        conn.commit()
        conn.close()

        result = tmp_chain.verify_chain()
        assert result.valid is False
        assert "Chain break" in result.message

    def test_genesis_non_null_prev_detected(self, tmp_chain):
        tmp_chain.write_bead(_make_signal())

        conn = sqlite3.connect(tmp_chain.db_path)
        conn.execute(
            "UPDATE beads SET prev_hash = ? WHERE seq = 1", ("a" * 64,)
        )
        conn.commit()
        conn.close()

        result = tmp_chain.verify_chain()
        assert result.valid is False
        assert "non-null prev_hash" in result.message


class TestChainStats:
    def test_empty_stats(self, tmp_chain):
        stats = tmp_chain.get_chain_stats()
        assert stats["chain_length"] == 0
        assert stats["type_counts"] == {}

    def test_stats_counts(self, tmp_chain):
        tmp_chain.write_bead(_make_signal())
        tmp_chain.write_bead(_make_signal())
        tmp_chain.write_bead(_make_heartbeat(1))

        stats = tmp_chain.get_chain_stats()
        assert stats["chain_length"] == 3
        assert stats["type_counts"]["signal"] == 2
        assert stats["type_counts"]["heartbeat"] == 1
        assert stats["unique_tokens"] == 1  # Both signals use "So111"

    def test_stats_time_range(self, tmp_chain):
        tmp_chain.write_bead(_make_signal())
        stats = tmp_chain.get_chain_stats()
        assert stats["earliest_bead"] is not None
        assert stats["latest_bead"] is not None


class TestChainExport:
    def test_export_jsonl(self, tmp_chain):
        for i in range(3):
            tmp_chain.write_bead(_make_heartbeat(i))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "export.jsonl"
            count = tmp_chain.export_chain_jsonl(path)
            assert count == 3

            lines = path.read_text().strip().split("\n")
            assert len(lines) == 3

            # Each line is valid JSON that can reconstruct a bead
            for line in lines:
                data = json.loads(line)
                bead = Bead.from_chain_dict(data)
                assert bead.header.bead_type == BeadType.HEARTBEAT

    def test_export_empty(self, tmp_chain):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "export.jsonl"
            count = tmp_chain.export_chain_jsonl(path)
            assert count == 0


class TestMixedBeadTypes:
    """Test realistic signal → verdict → trade → autopsy lifecycle."""

    def test_full_lifecycle(self, tmp_chain):
        # 1. Signal discovered
        signal = _make_signal(mint="PUMP_123", symbol="PUMP")
        sig_id = tmp_chain.write_bead(signal)

        # 2. Scored into verdict
        verdict = _make_verdict(mint="PUMP_123", derived=[sig_id])
        v_id = tmp_chain.write_bead(verdict)

        # 3. Paper trade executed
        trade = Bead(
            header=BeadHeader(bead_type=BeadType.TRADE),
            edges=BeadEdges(derived_from=[v_id]),
            payload=TradePayload(
                token_mint="PUMP_123",
                token_symbol="PUMP",
                play_type="graduation",
                conviction_score=72,
                recommendation="PAPER_TRADE",
                warden_verdict="PASS",
                entry_price=0.00012,
                entry_amount_sol=0.5,
                gate="paper",
            ),
        )
        t_id = tmp_chain.write_bead(trade)

        # 4. Autopsy after PnL check
        autopsy = Bead(
            header=BeadHeader(bead_type=BeadType.AUTOPSY),
            edges=BeadEdges(
                derived_from=[t_id],
                supports=[v_id],
            ),
            payload=AutopsyPayload(
                trade_bead_id=t_id,
                pnl_sol=0.15,
                pnl_pct=30.0,
                exit_reason="6h_expiry",
                hold_duration_seconds=21600,
                lesson="Volume thesis confirmed but slow exit.",
            ),
        )
        a_id = tmp_chain.write_bead(autopsy)

        # Verify chain
        result = tmp_chain.verify_chain()
        assert result.valid is True
        assert result.verified_beads == 4

        # Verify edge traversal
        sig_refs = tmp_chain.query_by_edge(sig_id)
        assert len(sig_refs) == 1  # verdict

        v_refs = tmp_chain.query_by_edge(v_id)
        assert len(v_refs) == 2  # trade + autopsy

        t_refs = tmp_chain.query_by_edge(t_id)
        assert len(t_refs) == 1  # autopsy

        # Verify token query (autopsy has no token_mint, so 3 not 4)
        token_beads = tmp_chain.query_by_token("PUMP_123")
        assert len(token_beads) == 3

        # Stats
        stats = tmp_chain.get_chain_stats()
        assert stats["chain_length"] == 4
        assert stats["unique_tokens"] == 1
