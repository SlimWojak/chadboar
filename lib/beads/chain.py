"""Bead chain manager — append-only SQLite storage with hash-linked integrity.

The BeadChain is ChadBoar's structured memory. Every bead is validated via
Pydantic, hash-linked to its predecessor, and stored with full edge/provenance
metadata in SQLite.

Replaces the old dual-system (lib/chain + lib/edge/bank) with a single
unified chain that supports type queries, edge traversal, and JSONL export.

DB file: state/beads.db (separate from legacy edge.db).

Ports to a8ra: per-agent chains, cross-agent edge queries, Gate-signed beads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.beads.schema import (
    GENESIS_PREV_HASH,
    Bead,
    BeadType,
)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = WORKSPACE / "state" / "beads.db"


@dataclass
class ChainVerifyResult:
    """Result of a chain integrity verification."""

    valid: bool
    total_beads: int
    verified_beads: int
    first_break_seq: int | None = None
    message: str = ""


class BeadChain:
    """Append-only bead chain backed by SQLite.

    Thread-safe via a reentrant lock. Each write validates the Pydantic
    schema, computes the content hash, links to the chain head, and
    appends atomically.
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Create the beads table if it doesn't exist."""
        with self._lock:
            conn = self._conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS beads (
                    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
                    bead_id     TEXT NOT NULL UNIQUE,
                    prev_hash   TEXT NOT NULL,
                    bead_type   TEXT NOT NULL,
                    timestamp   TEXT NOT NULL,
                    agent_id    TEXT NOT NULL,
                    session_id  TEXT NOT NULL DEFAULT '',
                    token_mint  TEXT NOT NULL DEFAULT '',
                    payload     TEXT NOT NULL,
                    edges       TEXT NOT NULL,
                    provenance  TEXT NOT NULL,
                    full_bead   TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_beads_type
                    ON beads (bead_type);
                CREATE INDEX IF NOT EXISTS idx_beads_token
                    ON beads (token_mint);
                CREATE INDEX IF NOT EXISTS idx_beads_timestamp
                    ON beads (timestamp);
            """)
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        """Open a new connection (SQLite connections aren't thread-safe)."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── Write ──────────────────────────────────────────────────────────

    def write_bead(self, bead: Bead) -> str:
        """Validate, hash, link, and append a bead. Returns bead_id.

        Steps:
        1. Compute bead_id from canonical content
        2. Set prev_hash from current chain head
        3. Insert into SQLite atomically
        """
        with self._lock:
            conn = self._conn()
            try:
                # Get chain head for prev_hash linkage
                head_row = conn.execute(
                    "SELECT bead_id FROM beads ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                bead.header.prev_hash = head_row[0] if head_row else GENESIS_PREV_HASH

                # Compute content hash (after prev_hash is set, but prev_hash
                # is excluded from canonical content by design)
                bead.header.bead_id = bead.compute_bead_id()

                # Extract token_mint for indexing (not all types have it)
                token_mint = ""
                if hasattr(bead.payload, "token_mint"):
                    token_mint = bead.payload.token_mint

                chain_dict = bead.to_chain_dict()

                conn.execute(
                    """INSERT INTO beads
                    (bead_id, prev_hash, bead_type, timestamp, agent_id,
                     session_id, token_mint, payload, edges, provenance, full_bead)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        bead.header.bead_id,
                        bead.header.prev_hash,
                        bead.header.bead_type.value,
                        bead.header.timestamp,
                        bead.header.agent_id,
                        bead.header.session_id,
                        token_mint,
                        json.dumps(chain_dict["payload"], sort_keys=True),
                        json.dumps(chain_dict["edges"], sort_keys=True),
                        json.dumps(chain_dict["provenance"], sort_keys=True),
                        json.dumps(chain_dict, sort_keys=True),
                    ),
                )
                conn.commit()
                return bead.header.bead_id
            finally:
                conn.close()

    # ── Read ───────────────────────────────────────────────────────────

    def get_bead(self, bead_id: str) -> Bead | None:
        """Retrieve a single bead by ID."""
        conn = self._conn()
        row = conn.execute(
            "SELECT full_bead FROM beads WHERE bead_id = ?", (bead_id,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return Bead.from_chain_dict(json.loads(row[0]))

    def get_chain_head(self) -> Bead | None:
        """Get the most recent bead in the chain."""
        conn = self._conn()
        row = conn.execute(
            "SELECT full_bead FROM beads ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return Bead.from_chain_dict(json.loads(row[0]))

    def get_chain_length(self) -> int:
        """Return the total number of beads in the chain."""
        conn = self._conn()
        count = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        conn.close()
        return count

    # ── Query ──────────────────────────────────────────────────────────

    def query_by_type(
        self, bead_type: BeadType | str, limit: int = 50
    ) -> list[Bead]:
        """Get beads of a specific type, most recent first."""
        type_val = bead_type.value if isinstance(bead_type, BeadType) else bead_type
        conn = self._conn()
        rows = conn.execute(
            "SELECT full_bead FROM beads WHERE bead_type = ? "
            "ORDER BY seq DESC LIMIT ?",
            (type_val, limit),
        ).fetchall()
        conn.close()
        return [Bead.from_chain_dict(json.loads(r[0])) for r in rows]

    def query_by_token(self, token_mint: str, limit: int = 50) -> list[Bead]:
        """Get all beads for a specific token, most recent first."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT full_bead FROM beads WHERE token_mint = ? "
            "ORDER BY seq DESC LIMIT ?",
            (token_mint, limit),
        ).fetchall()
        conn.close()
        return [Bead.from_chain_dict(json.loads(r[0])) for r in rows]

    def query_by_edge(self, bead_id: str) -> list[Bead]:
        """Find all beads that reference bead_id in any edge field.

        Searches derived_from, supports, and contradicts edges.
        """
        conn = self._conn()
        # SQLite JSON: search for bead_id in the edges JSON text
        # This is a text search — sufficient for hex hashes that won't
        # appear as substrings of other data
        pattern = f"%{bead_id}%"
        rows = conn.execute(
            "SELECT full_bead FROM beads WHERE edges LIKE ? "
            "ORDER BY seq DESC",
            (pattern,),
        ).fetchall()
        conn.close()

        # Post-filter: confirm the bead_id is actually in an edge list
        results = []
        for r in rows:
            bead = Bead.from_chain_dict(json.loads(r[0]))
            edges = bead.edges
            if (
                bead_id in edges.derived_from
                or bead_id in edges.supports
                or bead_id in edges.contradicts
            ):
                results.append(bead)
        return results

    # ── Verify ─────────────────────────────────────────────────────────

    def verify_chain(self) -> ChainVerifyResult:
        """Verify full chain integrity.

        Checks:
        1. Each bead's stored hash matches recomputed hash from content
        2. prev_hash links form an unbroken chain
        3. Genesis bead uses null sentinel
        """
        conn = self._conn()
        rows = conn.execute(
            "SELECT seq, bead_id, prev_hash, full_bead "
            "FROM beads ORDER BY seq ASC"
        ).fetchall()
        conn.close()

        total = len(rows)
        if total == 0:
            return ChainVerifyResult(
                valid=True, total_beads=0, verified_beads=0,
                message="Empty chain",
            )

        for i, (seq, stored_id, stored_prev, full_json) in enumerate(rows):
            bead = Bead.from_chain_dict(json.loads(full_json))
            recomputed_id = bead.compute_bead_id()

            # Check 1: hash matches content
            if recomputed_id != stored_id:
                return ChainVerifyResult(
                    valid=False, total_beads=total, verified_beads=i,
                    first_break_seq=seq,
                    message=(
                        f"Hash mismatch at seq {seq}: "
                        f"stored={stored_id[:16]}... "
                        f"recomputed={recomputed_id[:16]}..."
                    ),
                )

            # Check 2: prev_hash chain linkage
            if i == 0:
                if stored_prev != GENESIS_PREV_HASH:
                    return ChainVerifyResult(
                        valid=False, total_beads=total, verified_beads=0,
                        first_break_seq=seq,
                        message=f"Genesis bead has non-null prev_hash: {stored_prev[:16]}...",
                    )
            else:
                expected_prev = rows[i - 1][1]  # bead_id of previous row
                if stored_prev != expected_prev:
                    return ChainVerifyResult(
                        valid=False, total_beads=total, verified_beads=i,
                        first_break_seq=seq,
                        message=(
                            f"Chain break at seq {seq}: "
                            f"expected prev={expected_prev[:16]}... "
                            f"stored prev={stored_prev[:16]}..."
                        ),
                    )

        return ChainVerifyResult(
            valid=True, total_beads=total, verified_beads=total,
            message=f"Chain verified: {total} beads, integrity OK",
        )

    # ── Stats ──────────────────────────────────────────────────────────

    def get_chain_stats(self) -> dict[str, Any]:
        """Get chain statistics: counts by type, time range, health."""
        conn = self._conn()

        total = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]

        type_counts = {}
        for row in conn.execute(
            "SELECT bead_type, COUNT(*) FROM beads GROUP BY bead_type"
        ).fetchall():
            type_counts[row[0]] = row[1]

        time_range = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM beads"
        ).fetchone()

        unique_tokens = conn.execute(
            "SELECT COUNT(DISTINCT token_mint) FROM beads WHERE token_mint != ''"
        ).fetchone()[0]

        conn.close()

        return {
            "chain_length": total,
            "type_counts": type_counts,
            "unique_tokens": unique_tokens,
            "earliest_bead": time_range[0] if time_range[0] else None,
            "latest_bead": time_range[1] if time_range[1] else None,
        }

    # ── Export ─────────────────────────────────────────────────────────

    def export_chain_jsonl(self, output_path: Path | str) -> int:
        """Export the full chain to JSONL format. Returns bead count."""
        output = Path(output_path)
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
