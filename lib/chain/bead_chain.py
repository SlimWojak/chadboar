"""Core hash chain — ChainBead model, SQLite storage, append, verify, stats.

Provides a tamper-evident sequential chain of beads. Each bead's hash
depends on the previous bead's hash, forming an integrity chain.

Storage: `chain_beads` table in edge.db (separate from trade `beads` table).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DB_PATH = WORKSPACE / "edge.db"

ANCHOR_BATCH_SIZE = 50


class ChainBead(BaseModel):
    """A single bead in the hash chain."""

    seq: int = 0
    bead_hash: str = ""
    prev_hash: str = ""
    timestamp: str = ""
    bead_type: str = ""  # heartbeat | trade_entry | trade_exit | signal_eval |
                         # guard_alert | self_repair | escalation | state_change | anchor
    payload: dict[str, Any] = {}
    anchor_tx: str = ""


def _get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a connection to edge.db with chain_beads table ensured."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chain_beads (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            bead_hash TEXT NOT NULL UNIQUE,
            prev_hash TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            bead_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            anchor_tx TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn


def compute_bead_hash(payload: dict[str, Any], prev_hash: str, timestamp: str) -> str:
    """Compute deterministic SHA-256 hash for a bead.

    Hash = SHA-256(canonical_json(payload) + prev_hash + timestamp)
    Canonical JSON: sorted keys, no spaces, ensure_ascii.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    preimage = canonical + prev_hash + timestamp
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def get_chain_tip(db_path: Path | None = None) -> ChainBead | None:
    """Get the latest bead in the chain."""
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT seq, bead_hash, prev_hash, timestamp, bead_type, payload, anchor_tx "
        "FROM chain_beads ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row is None:
        return None

    return ChainBead(
        seq=row[0],
        bead_hash=row[1],
        prev_hash=row[2],
        timestamp=row[3],
        bead_type=row[4],
        payload=json.loads(row[5]),
        anchor_tx=row[6],
    )


def append_bead(
    bead_type: str,
    payload: dict[str, Any],
    db_path: Path | None = None,
) -> ChainBead:
    """Append a new bead to the chain.

    Computes hash linking to previous bead, stores in SQLite,
    and triggers auto-anchor if batch threshold reached.
    """
    conn = _get_conn(db_path)

    # Get previous hash
    tip_row = conn.execute(
        "SELECT bead_hash FROM chain_beads ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = tip_row[0] if tip_row else "0" * 64

    timestamp = datetime.now(timezone.utc).isoformat()
    bead_hash = compute_bead_hash(payload, prev_hash, timestamp)

    conn.execute(
        "INSERT INTO chain_beads (bead_hash, prev_hash, timestamp, bead_type, payload, anchor_tx) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (bead_hash, prev_hash, timestamp, bead_type, json.dumps(payload, sort_keys=True), ""),
    )
    conn.commit()

    # Get the assigned seq
    seq = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    bead = ChainBead(
        seq=seq,
        bead_hash=bead_hash,
        prev_hash=prev_hash,
        timestamp=timestamp,
        bead_type=bead_type,
        payload=payload,
        anchor_tx="",
    )

    # Auto-anchor check
    unanchored = get_beads_since_anchor(db_path)
    if len(unanchored) >= ANCHOR_BATCH_SIZE:
        try:
            from lib.chain.anchor import submit_anchor
            seq_start = unanchored[0].seq
            seq_end = unanchored[-1].seq
            anchor_result = submit_anchor(
                merkle_root="",  # computed inside submit_anchor
                seq_start=seq_start,
                seq_end=seq_end,
                bead_count=len(unanchored),
                db_path=db_path,
            )
            if anchor_result.get("status") == "OK":
                _mark_anchored(seq_start, seq_end, anchor_result["tx_signature"], db_path)
                # Write anchor bead
                anchor_payload = {
                    "tx_signature": anchor_result["tx_signature"],
                    "merkle_root": anchor_result["merkle_root"],
                    "seq_range": [seq_start, seq_end],
                    "bead_count": len(unanchored),
                }
                append_bead("anchor", anchor_payload, db_path)
        except Exception as e:
            # Anchoring is best-effort — don't block on failure
            print(f"[chain] Anchor failed (non-fatal): {e}", file=sys.stderr)

    return bead


def _mark_anchored(seq_start: int, seq_end: int, tx_sig: str, db_path: Path | None = None) -> None:
    """Mark a range of beads as anchored with a transaction signature."""
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE chain_beads SET anchor_tx = ? WHERE seq >= ? AND seq <= ?",
        (tx_sig, seq_start, seq_end),
    )
    conn.commit()
    conn.close()


def verify_chain(from_seq: int = 0, db_path: Path | None = None) -> tuple[bool, str]:
    """Verify hash chain integrity from seq N.

    Returns (is_valid, message). Checks that each bead's hash matches
    recomputation and that prev_hash links are correct.
    """
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT seq, bead_hash, prev_hash, timestamp, bead_type, payload "
        "FROM chain_beads WHERE seq >= ? ORDER BY seq ASC",
        (from_seq,),
    ).fetchall()
    conn.close()

    if not rows:
        return True, "No beads to verify"

    for i, row in enumerate(rows):
        seq, stored_hash, stored_prev, timestamp, bead_type, payload_json = row
        payload = json.loads(payload_json)

        # Verify hash
        computed_hash = compute_bead_hash(payload, stored_prev, timestamp)
        if computed_hash != stored_hash:
            return False, f"Hash mismatch at seq {seq}: stored={stored_hash[:16]}... computed={computed_hash[:16]}..."

        # Verify prev_hash linkage (except first bead in range)
        if i > 0:
            expected_prev = rows[i - 1][1]  # bead_hash of previous row
            if stored_prev != expected_prev:
                return False, f"Prev-hash chain break at seq {seq}: expected={expected_prev[:16]}... stored={stored_prev[:16]}..."
        elif from_seq == 0 and i == 0:
            # Genesis bead should have zero prev_hash
            if seq == 1 and stored_prev != "0" * 64:
                return False, f"Genesis bead has non-zero prev_hash: {stored_prev[:16]}..."

    return True, f"Chain verified: {len(rows)} beads from seq {rows[0][0]} to {rows[-1][0]}"


def get_beads_since_anchor(db_path: Path | None = None) -> list[ChainBead]:
    """Get all beads since the last anchor bead (or all if no anchors)."""
    conn = _get_conn(db_path)

    # Find the last anchor bead's seq
    anchor_row = conn.execute(
        "SELECT seq FROM chain_beads WHERE bead_type = 'anchor' ORDER BY seq DESC LIMIT 1"
    ).fetchone()

    if anchor_row:
        after_seq = anchor_row[0]
        rows = conn.execute(
            "SELECT seq, bead_hash, prev_hash, timestamp, bead_type, payload, anchor_tx "
            "FROM chain_beads WHERE seq > ? ORDER BY seq ASC",
            (after_seq,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT seq, bead_hash, prev_hash, timestamp, bead_type, payload, anchor_tx "
            "FROM chain_beads ORDER BY seq ASC"
        ).fetchall()

    conn.close()

    return [
        ChainBead(
            seq=r[0], bead_hash=r[1], prev_hash=r[2], timestamp=r[3],
            bead_type=r[4], payload=json.loads(r[5]), anchor_tx=r[6],
        )
        for r in rows
    ]


def get_chain_stats(db_path: Path | None = None) -> dict[str, Any]:
    """Get chain health statistics."""
    conn = _get_conn(db_path)

    total = conn.execute("SELECT COUNT(*) FROM chain_beads").fetchone()[0]

    last_anchor = conn.execute(
        "SELECT seq, bead_hash, timestamp, payload FROM chain_beads "
        "WHERE bead_type = 'anchor' ORDER BY seq DESC LIMIT 1"
    ).fetchone()

    unanchored_count = conn.execute(
        "SELECT COUNT(*) FROM chain_beads WHERE anchor_tx = '' AND bead_type != 'anchor'"
    ).fetchone()[0]

    # Count beads since last anchor
    if last_anchor:
        beads_since = conn.execute(
            "SELECT COUNT(*) FROM chain_beads WHERE seq > ?",
            (last_anchor[0],),
        ).fetchone()[0]
    else:
        beads_since = total

    conn.close()

    stats: dict[str, Any] = {
        "chain_length": total,
        "beads_since_anchor": beads_since,
        "unanchored_beads": unanchored_count,
    }

    if last_anchor:
        anchor_payload = json.loads(last_anchor[3])
        stats["last_anchor"] = {
            "seq": last_anchor[0],
            "bead_hash": last_anchor[1][:16] + "...",
            "timestamp": last_anchor[2],
            "tx_signature": anchor_payload.get("tx_signature", ""),
            "merkle_root": anchor_payload.get("merkle_root", ""),
        }
    else:
        stats["last_anchor"] = None

    return stats
