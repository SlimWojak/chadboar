"""Edge Bank — Trade autopsy bead storage + vector recall.

Stores trade beads as markdown files in beads/ and embeddings in SQLite.
Provides similarity search for pattern recognition across cycles.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

WORKSPACE = Path(__file__).resolve().parent.parent.parent
BEADS_DIR = WORKSPACE / "beads"
DB_PATH = WORKSPACE / "edge.db"


class Bead(BaseModel):
    """A single trade autopsy bead."""

    bead_id: str = ""
    timestamp: str = ""
    bead_type: str = "entry"  # entry | exit
    token_mint: str = ""
    token_symbol: str = ""
    direction: str = ""  # buy | sell
    amount_sol: float = 0.0
    price_usd: float = 0.0
    thesis: str = ""
    signals: list[str] = Field(default_factory=list)
    outcome: str = "pending"  # pending | win | loss
    pnl_pct: float = 0.0
    exit_reason: str = ""
    market_conditions: str = ""

    def to_text(self) -> str:
        """Convert to text for embedding."""
        parts = [
            f"Type: {self.bead_type} {self.direction}",
            f"Token: {self.token_symbol}",
            f"Thesis: {self.thesis}",
            f"Signals: {', '.join(self.signals)}",
            f"Outcome: {self.outcome} ({self.pnl_pct:+.1f}%)",
            f"Market: {self.market_conditions}",
        ]
        if self.exit_reason:
            parts.append(f"Exit reason: {self.exit_reason}")
        return " | ".join(parts)


class EdgeBank:
    """Bead storage with vector similarity search."""

    def __init__(self, db_path: Path | None = None, beads_dir: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.beads_dir = beads_dir or BEADS_DIR
        self.beads_dir.mkdir(parents=True, exist_ok=True)
        self._embedder: Any = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database with bead table."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beads (
                bead_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                bead_type TEXT NOT NULL,
                token_mint TEXT,
                token_symbol TEXT,
                direction TEXT,
                amount_sol REAL,
                price_usd REAL,
                thesis TEXT,
                signals TEXT,
                outcome TEXT DEFAULT 'pending',
                pnl_pct REAL DEFAULT 0.0,
                exit_reason TEXT DEFAULT '',
                market_conditions TEXT DEFAULT '',
                embedding BLOB
            )
        """)
        conn.commit()
        conn.close()

    def _get_embedder(self) -> Any:
        """Lazy-load sentence-transformers model."""
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                return None
        return self._embedder

    def _embed(self, text: str) -> bytes | None:
        """Generate embedding for text."""
        embedder = self._get_embedder()
        if embedder is None:
            return None
        import numpy as np
        embedding = embedder.encode(text, convert_to_numpy=True)
        return embedding.astype(np.float32).tobytes()

    def write_bead(self, bead: Bead) -> str:
        """Write a bead to disk (markdown) and database (with embedding)."""
        now = datetime.now(timezone.utc)
        bead.timestamp = now.isoformat()
        bead.bead_id = now.strftime("%Y%m%d_%H%M%S") + f"_{bead.bead_type}_{bead.token_symbol}"

        # Write markdown file
        md_path = self.beads_dir / f"{bead.bead_id}.md"
        md_content = f"""# Bead: {bead.bead_id}
**Time:** {bead.timestamp}
**Type:** {bead.bead_type} ({bead.direction})
**Token:** {bead.token_symbol} (`{bead.token_mint}`)
**Amount:** {bead.amount_sol:.4f} SOL @ ${bead.price_usd:.8f}

## Thesis
{bead.thesis}

## Signals
{chr(10).join(f'- {s}' for s in bead.signals) if bead.signals else 'None'}

## Outcome
- Result: {bead.outcome}
- PnL: {bead.pnl_pct:+.1f}%
- Exit reason: {bead.exit_reason or 'N/A'}

## Market Conditions
{bead.market_conditions}
"""
        md_path.write_text(md_content)

        # Generate embedding and store in DB
        embedding = self._embed(bead.to_text())
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO beads
            (bead_id, timestamp, bead_type, token_mint, token_symbol, direction,
             amount_sol, price_usd, thesis, signals, outcome, pnl_pct,
             exit_reason, market_conditions, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bead.bead_id, bead.timestamp, bead.bead_type, bead.token_mint,
                bead.token_symbol, bead.direction, bead.amount_sol, bead.price_usd,
                bead.thesis, json.dumps(bead.signals), bead.outcome, bead.pnl_pct,
                bead.exit_reason, bead.market_conditions, embedding,
            ),
        )
        conn.commit()
        conn.close()

        # Append to flight recorder chain (tamper-evident hash chain)
        try:
            from lib.chain.bead_chain import append_bead as chain_append
            chain_type = "trade_entry" if bead.bead_type == "entry" else "trade_exit"
            chain_append(chain_type, {
                "bead_id": bead.bead_id,
                "token_symbol": bead.token_symbol,
                "direction": bead.direction,
                "amount_sol": bead.amount_sol,
                "signals": bead.signals,
                "outcome": bead.outcome,
                "pnl_pct": bead.pnl_pct,
            })
        except Exception:
            pass  # Chain is best-effort — never block trade bead writes

        return bead.bead_id

    def query_similar(self, context: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Find beads most similar to the given context.

        Uses cosine similarity on embeddings. Falls back to recent beads
        if sentence-transformers is not available.
        """
        query_emb = self._embed(context)

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT bead_id, timestamp, token_symbol, thesis, outcome, pnl_pct, "
            "exit_reason, signals, embedding FROM beads ORDER BY timestamp DESC"
        ).fetchall()
        conn.close()

        if not rows:
            return []

        # If no embeddings available, return most recent
        if query_emb is None:
            return [
                {
                    "bead_id": r[0],
                    "date": r[1][:10],
                    "token_symbol": r[2],
                    "thesis": r[3],
                    "outcome": r[4],
                    "pnl_pct": r[5],
                    "exit_reason": r[6],
                    "similarity": 0.0,
                }
                for r in rows[:top_k]
            ]

        # Compute cosine similarities
        import numpy as np
        query_vec = np.frombuffer(query_emb, dtype=np.float32)
        scored = []
        for r in rows:
            if r[8] is None:
                continue
            bead_vec = np.frombuffer(r[8], dtype=np.float32)
            cos_sim = float(np.dot(query_vec, bead_vec) / (
                np.linalg.norm(query_vec) * np.linalg.norm(bead_vec) + 1e-8
            ))
            scored.append((cos_sim, r))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "similarity": round(score, 3),
                "bead_id": r[0],
                "date": r[1][:10],
                "token_symbol": r[2],
                "thesis": r[3],
                "outcome": r[4],
                "pnl_pct": r[5],
                "exit_reason": r[6],
                "signals": json.loads(r[7]) if r[7] else [],
            }
            for score, r in scored[:top_k]
        ]

    def get_stats(self) -> dict[str, Any]:
        """Get bead bank statistics."""
        conn = sqlite3.connect(self.db_path)
        total = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        wins = conn.execute("SELECT COUNT(*) FROM beads WHERE outcome='win'").fetchone()[0]
        losses = conn.execute("SELECT COUNT(*) FROM beads WHERE outcome='loss'").fetchone()[0]
        conn.close()
        return {"total_beads": total, "wins": wins, "losses": losses, "pending": total - wins - losses}
