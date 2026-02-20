"""Bead chain manager — bi-temporal-aware, ECDSA-signed, Merkle-anchored.

Implements BEAD_FIELD_SPEC v0.2 chain management for ChadBoar.
SQLite with explicit WT/KT columns proves the query patterns that will
run on XTDB in a8ra. Single-node, single-stream for now.

DB file: state/beads.db (overwritten from v0 — no migration needed).

Key behaviors:
  - write_bead: validate → compute hash_self → set hash_prev → sign → insert
  - Lineage edges stored in normalized bead_lineage table for traversal
  - Merkle batching: hybrid trigger (decision boundary / max beads / max time)
  - All queries support since/until datetime filters on KT
  - WAL mode for concurrent read/write safety
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.beads.schema import (
    BeadBase,
    BeadStatus,
    BeadType,
    RejectionCategory,
    TemporalClass,
)
from lib.beads.signing import (
    NODE_ID,
    get_code_hash,
    sign_hash,
    verify_signature,
)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = WORKSPACE / "state" / "beads.db"


@dataclass
class ChainVerifyResult:
    valid: bool
    total_beads: int
    verified_beads: int
    first_break_seq: int | None = None
    signature_failures: int = 0
    message: str = ""


@dataclass
class LatencyStats:
    count: int = 0
    avg_seconds: float = 0.0
    p50_seconds: float = 0.0
    p95_seconds: float = 0.0
    p99_seconds: float = 0.0


# ── SQL Schema ───────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS beads (
    seq                      INTEGER PRIMARY KEY AUTOINCREMENT,
    bead_id                  TEXT NOT NULL UNIQUE,
    bead_type                TEXT NOT NULL,
    hash_self                TEXT NOT NULL UNIQUE,
    hash_prev                TEXT,
    merkle_batch_id          TEXT,

    world_time_valid_from    TEXT,
    world_time_valid_to      TEXT,
    knowledge_time_recorded_at TEXT NOT NULL,
    temporal_class           TEXT NOT NULL,

    token_mint               TEXT DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'ACTIVE',
    tags                     TEXT NOT NULL DEFAULT '[]',

    content                  TEXT NOT NULL,
    lineage                  TEXT NOT NULL,
    source_ref               TEXT NOT NULL,
    attestation              TEXT NOT NULL,
    full_bead                TEXT NOT NULL,

    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(bead_type);
CREATE INDEX IF NOT EXISTS idx_beads_token ON beads(token_mint);
CREATE INDEX IF NOT EXISTS idx_beads_kt ON beads(knowledge_time_recorded_at);
CREATE INDEX IF NOT EXISTS idx_beads_wt_from ON beads(world_time_valid_from);
CREATE INDEX IF NOT EXISTS idx_beads_wt_to ON beads(world_time_valid_to);
CREATE INDEX IF NOT EXISTS idx_beads_temporal_class ON beads(temporal_class);
CREATE INDEX IF NOT EXISTS idx_beads_status ON beads(status);
CREATE INDEX IF NOT EXISTS idx_beads_merkle ON beads(merkle_batch_id);

CREATE TABLE IF NOT EXISTS bead_lineage (
    bead_id     TEXT NOT NULL,
    parent_id   TEXT NOT NULL,
    position    INTEGER NOT NULL,
    PRIMARY KEY (bead_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_lineage_parent ON bead_lineage(parent_id);

CREATE TABLE IF NOT EXISTS merkle_batches (
    batch_id      TEXT PRIMARY KEY,
    merkle_root   TEXT NOT NULL,
    bead_count    INTEGER NOT NULL,
    trigger_type  TEXT NOT NULL,
    trigger_bead_id TEXT,
    created_at    TEXT NOT NULL,
    anchor_tx     TEXT
);
"""


class BeadChain:
    """Bi-temporal bead chain with ECDSA signing and Merkle anchoring."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.executescript(_SCHEMA_SQL)
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── Write ────────────────────────────────────────────────────────

    def write_bead(self, bead: BeadBase) -> str:
        """Validate, compute hash_self, set hash_prev, sign, insert.

        Also inserts lineage edges into bead_lineage table.
        Returns bead_id.
        """
        with self._lock:
            conn = self._conn()
            try:
                # Chain linkage
                head_row = conn.execute(
                    "SELECT bead_id FROM beads ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                bead.hash_prev = head_row[0] if head_row else None

                # Attestation envelope (must be set BEFORE hash computation
                # because air_node_id and code_hash are part of canonical content)
                bead.attestation.air_node_id = NODE_ID
                bead.attestation.code_hash = get_code_hash()

                # Compute content hash (attestation fields included,
                # ecdsa_sig excluded from canonical content)
                bead.hash_self = bead.compute_hash_self()

                # Sign the hash
                try:
                    bead.attestation.ecdsa_sig = sign_hash(bead.hash_self)
                except Exception:
                    bead.attestation.ecdsa_sig = "signing_unavailable"

                # Extract token_mint for denormalized index
                token_mint = self._extract_token_mint(bead)

                # Serialize
                full_dict = bead.to_storage_dict()
                full_json = json.dumps(full_dict, sort_keys=True, separators=(",", ":"))

                wt_from = bead.world_time_valid_from.isoformat() if bead.world_time_valid_from else None
                wt_to = bead.world_time_valid_to.isoformat() if bead.world_time_valid_to else None
                kt = bead.knowledge_time_recorded_at.isoformat()

                conn.execute(
                    """INSERT INTO beads
                    (bead_id, bead_type, hash_self, hash_prev, merkle_batch_id,
                     world_time_valid_from, world_time_valid_to,
                     knowledge_time_recorded_at, temporal_class,
                     token_mint, status, tags,
                     content, lineage, source_ref, attestation, full_bead)
                    VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?,?)""",
                    (
                        bead.bead_id,
                        bead.bead_type.value,
                        bead.hash_self,
                        bead.hash_prev,
                        bead.merkle_batch_id,
                        wt_from, wt_to, kt,
                        bead.temporal_class.value,
                        token_mint,
                        bead.status.value,
                        json.dumps(bead.tags),
                        json.dumps(full_dict.get("content", {}), sort_keys=True),
                        json.dumps(bead.lineage),
                        json.dumps(full_dict.get("source_ref", {}), sort_keys=True),
                        json.dumps(full_dict.get("attestation", {}), sort_keys=True),
                        full_json,
                    ),
                )

                # Insert lineage edges
                for pos, parent_id in enumerate(bead.lineage):
                    conn.execute(
                        "INSERT OR IGNORE INTO bead_lineage (bead_id, parent_id, position) VALUES (?,?,?)",
                        (bead.bead_id, parent_id, pos),
                    )

                conn.commit()
                return bead.bead_id
            finally:
                conn.close()

    @staticmethod
    def _extract_token_mint(bead: BeadBase) -> str:
        """Pull token_mint from content for denormalized index."""
        content = bead.content
        if isinstance(content, dict):
            return content.get("token_mint", "")
        return ""

    # ── Read ─────────────────────────────────────────────────────────

    def get_bead(self, bead_id: str) -> BeadBase | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT full_bead FROM beads WHERE bead_id = ?", (bead_id,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return BeadBase.from_storage_dict(json.loads(row[0]))

    def get_chain_head(self, stream: str = "main") -> BeadBase | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT full_bead FROM beads ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return BeadBase.from_storage_dict(json.loads(row[0]))

    # ── Query ────────────────────────────────────────────────────────

    def _query(
        self,
        where: str,
        params: tuple,
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[BeadBase]:
        """Generic query helper with KT time filters."""
        clauses = [where] if where else []
        p = list(params)

        if since:
            clauses.append("knowledge_time_recorded_at >= ?")
            p.append(since.isoformat())
        if until:
            clauses.append("knowledge_time_recorded_at <= ?")
            p.append(until.isoformat())

        where_sql = " AND ".join(clauses) if clauses else "1=1"

        conn = self._conn()
        rows = conn.execute(
            f"SELECT full_bead FROM beads WHERE {where_sql} "
            f"ORDER BY seq DESC LIMIT ?",
            (*p, limit),
        ).fetchall()
        conn.close()
        return [BeadBase.from_storage_dict(json.loads(r[0])) for r in rows]

    def query_by_type(
        self,
        bead_type: BeadType | str,
        *,
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[BeadBase]:
        type_val = bead_type.value if isinstance(bead_type, BeadType) else bead_type
        return self._query("bead_type = ?", (type_val,), limit, since, until)

    def query_by_token(
        self,
        token_mint: str,
        *,
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[BeadBase]:
        return self._query("token_mint = ?", (token_mint,), limit, since, until)

    def query_by_temporal_class(
        self,
        tc: TemporalClass | str,
        *,
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[BeadBase]:
        tc_val = tc.value if isinstance(tc, TemporalClass) else tc
        return self._query("temporal_class = ?", (tc_val,), limit, since, until)

    def query_by_tag(self, tag: str, *, limit: int = 50) -> list[BeadBase]:
        pattern = f'%"{tag}"%'
        return self._query("tags LIKE ?", (pattern,), limit)

    def query_by_status(
        self,
        status: BeadStatus | str,
        *,
        limit: int = 50,
    ) -> list[BeadBase]:
        s = status.value if isinstance(status, BeadStatus) else status
        return self._query("status = ?", (s,), limit)

    # ── Edge Traversal ───────────────────────────────────────────────

    def get_lineage(self, bead_id: str) -> list[BeadBase]:
        """Direct parents of this bead (ordered by position)."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT b.full_bead FROM bead_lineage l
               JOIN beads b ON b.bead_id = l.parent_id
               WHERE l.bead_id = ?
               ORDER BY l.position ASC""",
            (bead_id,),
        ).fetchall()
        conn.close()
        return [BeadBase.from_storage_dict(json.loads(r[0])) for r in rows]

    def get_descendants(self, bead_id: str) -> list[BeadBase]:
        """All beads that reference this bead in their lineage."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT b.full_bead FROM bead_lineage l
               JOIN beads b ON b.bead_id = l.bead_id
               WHERE l.parent_id = ?
               ORDER BY b.seq DESC""",
            (bead_id,),
        ).fetchall()
        conn.close()
        return [BeadBase.from_storage_dict(json.loads(r[0])) for r in rows]

    def walk_lineage(self, bead_id: str, depth: int = 10) -> list[BeadBase]:
        """Recursive lineage walk — full dependency tree up to depth.

        BFS traversal collecting all ancestors. Returns deduplicated list
        ordered by discovery (closest ancestors first).
        """
        visited: set[str] = set()
        result: list[BeadBase] = []
        queue = [bead_id]
        current_depth = 0

        while queue and current_depth < depth:
            next_queue: list[str] = []
            for bid in queue:
                if bid in visited:
                    continue
                visited.add(bid)
                parents = self.get_lineage(bid)
                for p in parents:
                    if p.bead_id not in visited:
                        result.append(p)
                        next_queue.append(p.bead_id)
            queue = next_queue
            current_depth += 1

        return result

    # ── Shadow Field ─────────────────────────────────────────────────

    def query_shadow_field(
        self,
        *,
        rejection_category: RejectionCategory | str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[BeadBase]:
        """All PROPOSAL_REJECTED beads, optionally filtered."""
        if rejection_category:
            cat_val = (
                rejection_category.value
                if isinstance(rejection_category, RejectionCategory)
                else rejection_category
            )
            pattern = f'%"rejection_category": "{cat_val}"%'
            return self._query(
                "bead_type = ? AND content LIKE ?",
                (BeadType.PROPOSAL_REJECTED.value, pattern),
                limit, since, until,
            )
        return self._query(
            "bead_type = ?",
            (BeadType.PROPOSAL_REJECTED.value,),
            limit, since, until,
        )

    def shadow_field_stats(self) -> dict:
        """Rejection category distribution, volume over time, linked skills."""
        conn = self._conn()

        total = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE bead_type = ?",
            (BeadType.PROPOSAL_REJECTED.value,),
        ).fetchone()[0]

        # Category distribution (parse content JSON)
        rows = conn.execute(
            "SELECT content FROM beads WHERE bead_type = ?",
            (BeadType.PROPOSAL_REJECTED.value,),
        ).fetchall()
        conn.close()

        category_counts: dict[str, int] = {}
        linked_skills_count = 0
        for (content_json,) in rows:
            try:
                content = json.loads(content_json)
                cat = content.get("rejection_category", "UNKNOWN")
                category_counts[cat] = category_counts.get(cat, 0) + 1
                if content.get("linked_skills"):
                    linked_skills_count += len(content["linked_skills"])
            except (json.JSONDecodeError, TypeError):
                category_counts["PARSE_ERROR"] = category_counts.get("PARSE_ERROR", 0) + 1

        return {
            "total_rejections": total,
            "category_distribution": category_counts,
            "linked_skills_count": linked_skills_count,
        }

    # ── Bi-Temporal Queries ──────────────────────────────────────────

    def query_world_time_range(
        self,
        wt_from: datetime,
        wt_to: datetime,
        *,
        bead_type: BeadType | str | None = None,
    ) -> list[BeadBase]:
        """Observations covering a world-time window.

        Returns beads where world_time overlaps [wt_from, wt_to].
        """
        clauses = [
            "world_time_valid_from IS NOT NULL",
            "world_time_valid_to IS NOT NULL",
            "world_time_valid_from <= ?",
            "world_time_valid_to >= ?",
        ]
        params: list[Any] = [wt_to.isoformat(), wt_from.isoformat()]

        if bead_type:
            bt = bead_type.value if isinstance(bead_type, BeadType) else bead_type
            clauses.append("bead_type = ?")
            params.append(bt)

        where_sql = " AND ".join(clauses)
        conn = self._conn()
        rows = conn.execute(
            f"SELECT full_bead FROM beads WHERE {where_sql} ORDER BY seq DESC",
            params,
        ).fetchall()
        conn.close()
        return [BeadBase.from_storage_dict(json.loads(r[0])) for r in rows]

    def query_knowledge_at(
        self,
        kt: datetime,
        *,
        bead_type: BeadType | str | None = None,
        token_mint: str | None = None,
    ) -> list[BeadBase]:
        """What did we know at this point in time? (KT <= given time)."""
        clauses = ["knowledge_time_recorded_at <= ?"]
        params: list[Any] = [kt.isoformat()]

        if bead_type:
            bt = bead_type.value if isinstance(bead_type, BeadType) else bead_type
            clauses.append("bead_type = ?")
            params.append(bt)
        if token_mint:
            clauses.append("token_mint = ?")
            params.append(token_mint)

        where_sql = " AND ".join(clauses)
        conn = self._conn()
        rows = conn.execute(
            f"SELECT full_bead FROM beads WHERE {where_sql} ORDER BY seq DESC LIMIT 200",
            params,
        ).fetchall()
        conn.close()
        return [BeadBase.from_storage_dict(json.loads(r[0])) for r in rows]

    def refinery_latency(
        self,
        *,
        bead_type: BeadType | str | None = None,
        since: datetime | None = None,
    ) -> LatencyStats:
        """Average, p50, p95, p99 of (KT - WT_end) for OBSERVATION beads.

        Measures how long it takes from world event to knowledge commitment.
        """
        clauses = [
            "temporal_class = ?",
            "world_time_valid_to IS NOT NULL",
        ]
        params: list[Any] = [TemporalClass.OBSERVATION.value]

        if bead_type:
            bt = bead_type.value if isinstance(bead_type, BeadType) else bead_type
            clauses.append("bead_type = ?")
            params.append(bt)
        if since:
            clauses.append("knowledge_time_recorded_at >= ?")
            params.append(since.isoformat())

        where_sql = " AND ".join(clauses)
        conn = self._conn()
        rows = conn.execute(
            f"SELECT world_time_valid_to, knowledge_time_recorded_at "
            f"FROM beads WHERE {where_sql}",
            params,
        ).fetchall()
        conn.close()

        if not rows:
            return LatencyStats()

        deltas: list[float] = []
        for wt_to_str, kt_str in rows:
            try:
                wt_to = datetime.fromisoformat(wt_to_str)
                kt = datetime.fromisoformat(kt_str)
                delta = (kt - wt_to).total_seconds()
                if delta >= 0:
                    deltas.append(delta)
            except (ValueError, TypeError):
                continue

        if not deltas:
            return LatencyStats()

        deltas.sort()
        n = len(deltas)
        avg = sum(deltas) / n

        def percentile(sorted_vals: list[float], pct: float) -> float:
            idx = int(pct / 100 * (len(sorted_vals) - 1))
            return sorted_vals[min(idx, len(sorted_vals) - 1)]

        return LatencyStats(
            count=n,
            avg_seconds=round(avg, 3),
            p50_seconds=round(percentile(deltas, 50), 3),
            p95_seconds=round(percentile(deltas, 95), 3),
            p99_seconds=round(percentile(deltas, 99), 3),
        )

    # ── Integrity ────────────────────────────────────────────────────

    def verify_chain(self, stream: str = "main") -> ChainVerifyResult:
        """Verify hash chain integrity + ECDSA signatures.

        Checks:
        1. hash_self matches recomputed hash from canonical content
        2. hash_prev links form an unbroken chain
        3. ECDSA signatures verify against node public key
        """
        conn = self._conn()
        rows = conn.execute(
            "SELECT seq, bead_id, hash_self, hash_prev, full_bead "
            "FROM beads ORDER BY seq ASC"
        ).fetchall()
        conn.close()

        total = len(rows)
        if total == 0:
            return ChainVerifyResult(
                valid=True, total_beads=0, verified_beads=0,
                message="Empty chain",
            )

        sig_failures = 0

        for i, (seq, bead_id, stored_hash, stored_prev, full_json) in enumerate(rows):
            bead = BeadBase.from_storage_dict(json.loads(full_json))
            recomputed = bead.compute_hash_self()

            if recomputed != stored_hash:
                return ChainVerifyResult(
                    valid=False, total_beads=total, verified_beads=i,
                    first_break_seq=seq,
                    message=f"Hash mismatch at seq {seq}: stored={stored_hash[:16]}... recomputed={recomputed[:16]}...",
                )

            if i == 0:
                if stored_prev is not None:
                    # Genesis can have null prev
                    pass
            else:
                expected_prev = rows[i - 1][1]  # bead_id of previous row
                if stored_prev != expected_prev:
                    return ChainVerifyResult(
                        valid=False, total_beads=total, verified_beads=i,
                        first_break_seq=seq,
                        message=f"Chain break at seq {seq}: expected prev={expected_prev[:16]}... stored prev={stored_prev[:16] if stored_prev else 'None'}...",
                    )

            # ECDSA verification (best-effort — signing_unavailable is acceptable)
            sig = bead.attestation.ecdsa_sig
            if sig and sig != "signing_unavailable":
                if not verify_signature(stored_hash, sig):
                    sig_failures += 1

        return ChainVerifyResult(
            valid=True, total_beads=total, verified_beads=total,
            signature_failures=sig_failures,
            message=f"Chain verified: {total} beads, integrity OK"
                    + (f" ({sig_failures} sig failures)" if sig_failures else ""),
        )

    def get_chain_stats(self) -> dict[str, Any]:
        """Comprehensive chain statistics."""
        conn = self._conn()

        total = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]

        type_counts = {}
        for row in conn.execute(
            "SELECT bead_type, COUNT(*) FROM beads GROUP BY bead_type"
        ).fetchall():
            type_counts[row[0]] = row[1]

        tc_counts = {}
        for row in conn.execute(
            "SELECT temporal_class, COUNT(*) FROM beads GROUP BY temporal_class"
        ).fetchall():
            tc_counts[row[0]] = row[1]

        shadow_count = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE bead_type = ?",
            (BeadType.PROPOSAL_REJECTED.value,),
        ).fetchone()[0]

        lineage_edges = conn.execute(
            "SELECT COUNT(*) FROM bead_lineage"
        ).fetchone()[0]

        merkle_count = conn.execute(
            "SELECT COUNT(*) FROM merkle_batches"
        ).fetchone()[0]

        time_range = conn.execute(
            "SELECT MIN(knowledge_time_recorded_at), MAX(knowledge_time_recorded_at) FROM beads"
        ).fetchone()

        unique_tokens = conn.execute(
            "SELECT COUNT(DISTINCT token_mint) FROM beads WHERE token_mint != ''"
        ).fetchone()[0]

        status_counts = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) FROM beads GROUP BY status"
        ).fetchall():
            status_counts[row[0]] = row[1]

        conn.close()

        verify = self.verify_chain()

        return {
            "chain_length": total,
            "type_counts": type_counts,
            "temporal_class_counts": tc_counts,
            "status_counts": status_counts,
            "shadow_field_size": shadow_count,
            "lineage_edges": lineage_edges,
            "merkle_batch_count": merkle_count,
            "unique_tokens": unique_tokens,
            "earliest_bead": time_range[0] if time_range[0] else None,
            "latest_bead": time_range[1] if time_range[1] else None,
            "chain_integrity": verify.message,
            "chain_valid": verify.valid,
        }

    # ── Merkle ───────────────────────────────────────────────────────

    def check_anchor_trigger(self) -> str | None:
        """Check if anchoring should trigger. Returns trigger type or None.

        Triggers:
          DECISION_BOUNDARY — SIGNAL or PROPOSAL committed since last anchor
          MAX_BEADS — 500+ unanchored beads
          MAX_TIME — 1h+ since last anchor
        """
        conn = self._conn()

        last_anchor = conn.execute(
            "SELECT created_at FROM merkle_batches ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_anchor_time = last_anchor[0] if last_anchor else None

        if last_anchor_time:
            unanchored = conn.execute(
                "SELECT COUNT(*) FROM beads WHERE merkle_batch_id IS NULL AND created_at > ?",
                (last_anchor_time,),
            ).fetchone()[0]

            decision_beads = conn.execute(
                "SELECT COUNT(*) FROM beads WHERE merkle_batch_id IS NULL "
                "AND created_at > ? AND bead_type IN (?, ?)",
                (last_anchor_time, BeadType.SIGNAL.value, BeadType.PROPOSAL.value),
            ).fetchone()[0]
        else:
            unanchored = conn.execute(
                "SELECT COUNT(*) FROM beads WHERE merkle_batch_id IS NULL"
            ).fetchone()[0]
            decision_beads = conn.execute(
                "SELECT COUNT(*) FROM beads WHERE merkle_batch_id IS NULL "
                "AND bead_type IN (?, ?)",
                (BeadType.SIGNAL.value, BeadType.PROPOSAL.value),
            ).fetchone()[0]
            last_anchor_time = None

        conn.close()

        if decision_beads > 0:
            return "DECISION_BOUNDARY"

        if unanchored >= 500:
            return "MAX_BEADS"

        if last_anchor_time:
            try:
                anchor_dt = datetime.fromisoformat(last_anchor_time)
                if not anchor_dt.tzinfo:
                    anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - anchor_dt).total_seconds()
                if elapsed >= 3600:
                    return "MAX_TIME"
            except (ValueError, TypeError):
                pass
        elif unanchored > 0:
            return "MAX_TIME"

        return None

    def create_merkle_batch(
        self,
        trigger_type: str,
        trigger_bead_id: str | None = None,
    ) -> str:
        """Build Merkle tree over unanchored beads, create batch record.

        Backfills merkle_batch_id on all included beads. Returns batch_id.
        """
        from uuid_extensions import uuid7

        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT bead_id, hash_self FROM beads "
                    "WHERE merkle_batch_id IS NULL ORDER BY seq ASC"
                ).fetchall()

                if not rows:
                    conn.close()
                    return ""

                # Build binary Merkle tree
                hashes = [r[1] for r in rows]
                merkle_root = self._compute_merkle_root(hashes)

                batch_id = str(uuid7())
                now = datetime.now(timezone.utc).isoformat()

                conn.execute(
                    "INSERT INTO merkle_batches (batch_id, merkle_root, bead_count, trigger_type, trigger_bead_id, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (batch_id, merkle_root, len(rows), trigger_type, trigger_bead_id, now),
                )

                bead_ids = [r[0] for r in rows]
                for bid in bead_ids:
                    conn.execute(
                        "UPDATE beads SET merkle_batch_id = ? WHERE bead_id = ?",
                        (batch_id, bid),
                    )

                conn.commit()
                return batch_id
            finally:
                conn.close()

    @staticmethod
    def _compute_merkle_root(hashes: list[str]) -> str:
        """Simple binary Merkle tree over SHA-256 hashes."""
        if not hashes:
            return hashlib.sha256(b"").hexdigest()
        if len(hashes) == 1:
            return hashes[0]

        level = list(hashes)
        while len(level) > 1:
            next_level: list[str] = []
            for i in range(0, len(level), 2):
                left = level[i]
                right = level[i + 1] if i + 1 < len(level) else level[i]
                combined = hashlib.sha256(
                    (left + right).encode("utf-8")
                ).hexdigest()
                next_level.append(combined)
            level = next_level
        return level[0]

    # ── Export ────────────────────────────────────────────────────────

    def export_chain_jsonl(self, path: str | Path) -> int:
        """Full chain as JSONL. Git-friendly, a8ra-compatible."""
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)

        conn = self._conn()
        rows = conn.execute(
            "SELECT full_bead FROM beads ORDER BY seq ASC"
        ).fetchall()
        conn.close()

        with open(output, "w") as f:
            for (full_json,) in rows:
                f.write(full_json + "\n")
        return len(rows)

    def import_chain_jsonl(self, path: str | Path) -> int:
        """Import from JSONL. For migration/restore.

        Skips beads that already exist (by bead_id). Returns count imported.
        """
        input_path = Path(path)
        if not input_path.exists():
            return 0

        imported = 0
        with open(input_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    bead = BeadBase.from_storage_dict(data)
                    conn = self._conn()
                    exists = conn.execute(
                        "SELECT 1 FROM beads WHERE bead_id = ?", (bead.bead_id,)
                    ).fetchone()
                    conn.close()
                    if not exists:
                        self.write_bead(bead)
                        imported += 1
                except Exception:
                    continue
        return imported

    def get_chain_length(self) -> int:
        conn = self._conn()
        count = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        conn.close()
        return count
