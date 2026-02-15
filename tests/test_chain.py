"""Tests for Cognitive Flight Recorder — hash chain + Merkle tree."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from lib.chain.merkle import build_merkle_tree, compute_merkle_root
from lib.chain.bead_chain import (
    ChainBead,
    append_bead,
    compute_bead_hash,
    get_beads_since_anchor,
    get_chain_stats,
    get_chain_tip,
    verify_chain,
)


# --- Merkle tree tests ---


class TestMerkleRoot:
    def test_empty_returns_zero_hash(self):
        assert compute_merkle_root([]) == "0" * 64

    def test_single_leaf(self):
        h = hashlib.sha256(b"test").hexdigest()
        # Single leaf — returned as-is (no pairing needed)
        assert compute_merkle_root([h]) == h

    def test_two_leaves(self):
        h1 = hashlib.sha256(b"a").hexdigest()
        h2 = hashlib.sha256(b"b").hexdigest()
        expected = hashlib.sha256(bytes.fromhex(h1) + bytes.fromhex(h2)).hexdigest()
        assert compute_merkle_root([h1, h2]) == expected

    def test_four_leaves(self):
        hashes = [hashlib.sha256(x).hexdigest() for x in [b"a", b"b", b"c", b"d"]]
        p01 = hashlib.sha256(bytes.fromhex(hashes[0]) + bytes.fromhex(hashes[1])).hexdigest()
        p23 = hashlib.sha256(bytes.fromhex(hashes[2]) + bytes.fromhex(hashes[3])).hexdigest()
        expected = hashlib.sha256(bytes.fromhex(p01) + bytes.fromhex(p23)).hexdigest()
        assert compute_merkle_root(hashes) == expected

    def test_odd_leaves_duplicate_last(self):
        hashes = [hashlib.sha256(x).hexdigest() for x in [b"a", b"b", b"c"]]
        p01 = hashlib.sha256(bytes.fromhex(hashes[0]) + bytes.fromhex(hashes[1])).hexdigest()
        p22 = hashlib.sha256(bytes.fromhex(hashes[2]) + bytes.fromhex(hashes[2])).hexdigest()
        expected = hashlib.sha256(bytes.fromhex(p01) + bytes.fromhex(p22)).hexdigest()
        assert compute_merkle_root(hashes) == expected

    def test_deterministic(self):
        hashes = [hashlib.sha256(f"bead_{i}".encode()).hexdigest() for i in range(10)]
        r1 = compute_merkle_root(hashes)
        r2 = compute_merkle_root(hashes)
        assert r1 == r2

    def test_build_tree_root_matches(self):
        hashes = [hashlib.sha256(f"bead_{i}".encode()).hexdigest() for i in range(7)]
        root = compute_merkle_root(hashes)
        tree = build_merkle_tree(hashes)
        assert tree[-1][0] == root
        assert tree[0] == hashes

    def test_build_tree_empty(self):
        tree = build_merkle_tree([])
        assert tree == [["0" * 64]]


# --- Hash computation tests ---


class TestComputeBeadHash:
    def test_deterministic(self):
        payload = {"key": "value", "num": 42}
        prev = "a" * 64
        ts = "2026-02-15T00:00:00+00:00"
        h1 = compute_bead_hash(payload, prev, ts)
        h2 = compute_bead_hash(payload, prev, ts)
        assert h1 == h2

    def test_different_payload_different_hash(self):
        prev = "a" * 64
        ts = "2026-02-15T00:00:00+00:00"
        h1 = compute_bead_hash({"a": 1}, prev, ts)
        h2 = compute_bead_hash({"a": 2}, prev, ts)
        assert h1 != h2

    def test_different_prev_different_hash(self):
        payload = {"a": 1}
        ts = "2026-02-15T00:00:00+00:00"
        h1 = compute_bead_hash(payload, "a" * 64, ts)
        h2 = compute_bead_hash(payload, "b" * 64, ts)
        assert h1 != h2

    def test_key_order_irrelevant(self):
        """Canonical JSON sorts keys, so order shouldn't matter."""
        prev = "0" * 64
        ts = "2026-02-15T00:00:00+00:00"
        h1 = compute_bead_hash({"b": 2, "a": 1}, prev, ts)
        h2 = compute_bead_hash({"a": 1, "b": 2}, prev, ts)
        assert h1 == h2

    def test_hash_is_hex_sha256(self):
        h = compute_bead_hash({"x": 1}, "0" * 64, "2026-01-01T00:00:00+00:00")
        assert len(h) == 64
        int(h, 16)  # Should not raise — valid hex


# --- Chain storage tests (use temp DB) ---


@pytest.fixture
def tmp_db():
    """Create a temporary database for chain tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_edge.db"


class TestChainAppend:
    def test_genesis_bead(self, tmp_db):
        bead = append_bead("heartbeat", {"cycle": 1}, db_path=tmp_db)
        assert bead.seq == 1
        assert bead.prev_hash == "0" * 64
        assert bead.bead_type == "heartbeat"
        assert bead.payload == {"cycle": 1}
        assert len(bead.bead_hash) == 64

    def test_chain_linkage(self, tmp_db):
        b1 = append_bead("heartbeat", {"cycle": 1}, db_path=tmp_db)
        b2 = append_bead("heartbeat", {"cycle": 2}, db_path=tmp_db)
        b3 = append_bead("trade_entry", {"token": "BOAR"}, db_path=tmp_db)

        assert b2.prev_hash == b1.bead_hash
        assert b3.prev_hash == b2.bead_hash

    def test_unique_hashes(self, tmp_db):
        beads = [append_bead("heartbeat", {"cycle": i}, db_path=tmp_db) for i in range(5)]
        hashes = [b.bead_hash for b in beads]
        assert len(set(hashes)) == 5

    def test_get_chain_tip(self, tmp_db):
        assert get_chain_tip(tmp_db) is None

        b1 = append_bead("heartbeat", {"cycle": 1}, db_path=tmp_db)
        tip = get_chain_tip(tmp_db)
        assert tip is not None
        assert tip.seq == b1.seq
        assert tip.bead_hash == b1.bead_hash

        b2 = append_bead("heartbeat", {"cycle": 2}, db_path=tmp_db)
        tip = get_chain_tip(tmp_db)
        assert tip is not None
        assert tip.seq == b2.seq


class TestChainVerify:
    def test_valid_chain(self, tmp_db):
        for i in range(10):
            append_bead("heartbeat", {"cycle": i}, db_path=tmp_db)
        valid, msg = verify_chain(db_path=tmp_db)
        assert valid is True
        assert "10 beads" in msg

    def test_empty_chain(self, tmp_db):
        valid, msg = verify_chain(db_path=tmp_db)
        assert valid is True

    def test_tampered_hash_detected(self, tmp_db):
        for i in range(5):
            append_bead("heartbeat", {"cycle": i}, db_path=tmp_db)

        # Tamper with bead 3's hash
        conn = sqlite3.connect(tmp_db)
        conn.execute("UPDATE chain_beads SET bead_hash = ? WHERE seq = 3", ("f" * 64,))
        conn.commit()
        conn.close()

        valid, msg = verify_chain(db_path=tmp_db)
        assert valid is False
        assert "seq 3" in msg

    def test_tampered_payload_detected(self, tmp_db):
        for i in range(5):
            append_bead("heartbeat", {"cycle": i}, db_path=tmp_db)

        # Tamper with bead 2's payload
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            'UPDATE chain_beads SET payload = ? WHERE seq = 2',
            (json.dumps({"cycle": 999}),),
        )
        conn.commit()
        conn.close()

        valid, msg = verify_chain(db_path=tmp_db)
        assert valid is False
        assert "Hash mismatch" in msg

    def test_broken_prev_link_detected(self, tmp_db):
        for i in range(5):
            append_bead("heartbeat", {"cycle": i}, db_path=tmp_db)

        # Break the prev_hash link at bead 4
        conn = sqlite3.connect(tmp_db)
        conn.execute("UPDATE chain_beads SET prev_hash = ? WHERE seq = 4", ("0" * 64,))
        # Also fix the hash to match the new prev_hash so we test linkage specifically
        row = conn.execute(
            "SELECT timestamp, payload FROM chain_beads WHERE seq = 4"
        ).fetchone()
        new_hash = compute_bead_hash(json.loads(row[1]), "0" * 64, row[0])
        conn.execute("UPDATE chain_beads SET bead_hash = ? WHERE seq = 4", (new_hash,))
        conn.commit()
        conn.close()

        valid, msg = verify_chain(db_path=tmp_db)
        assert valid is False
        assert "chain break" in msg

    def test_verify_from_seq(self, tmp_db):
        for i in range(10):
            append_bead("heartbeat", {"cycle": i}, db_path=tmp_db)
        valid, msg = verify_chain(from_seq=5, db_path=tmp_db)
        assert valid is True


class TestChainStats:
    def test_empty_stats(self, tmp_db):
        stats = get_chain_stats(tmp_db)
        assert stats["chain_length"] == 0
        assert stats["last_anchor"] is None

    def test_stats_after_beads(self, tmp_db):
        for i in range(5):
            append_bead("heartbeat", {"cycle": i}, db_path=tmp_db)
        stats = get_chain_stats(tmp_db)
        assert stats["chain_length"] == 5
        assert stats["beads_since_anchor"] == 5
        assert stats["last_anchor"] is None

    def test_beads_since_anchor(self, tmp_db):
        for i in range(3):
            append_bead("heartbeat", {"cycle": i}, db_path=tmp_db)

        # Simulate an anchor bead
        append_bead("anchor", {
            "tx_signature": "fake_tx",
            "merkle_root": "a" * 64,
            "seq_range": [1, 3],
            "bead_count": 3,
        }, db_path=tmp_db)

        # Add more beads after anchor
        for i in range(2):
            append_bead("heartbeat", {"cycle": i + 10}, db_path=tmp_db)

        unanchored = get_beads_since_anchor(tmp_db)
        assert len(unanchored) == 2

        stats = get_chain_stats(tmp_db)
        assert stats["chain_length"] == 6  # 3 + 1 anchor + 2
        assert stats["beads_since_anchor"] == 2
        assert stats["last_anchor"] is not None
        assert stats["last_anchor"]["tx_signature"] == "fake_tx"
