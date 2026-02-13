"""Tests for Edge Bank — bead persistence + vector recall.

Tests bead writing, querying, and SQLite storage.
Sentence-transformers is optional; tests degrade gracefully without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.edge.bank import Bead, EdgeBank


@pytest.fixture
def bank(tmp_path):
    """Create an EdgeBank with isolated temp storage."""
    return EdgeBank(
        db_path=tmp_path / "test_edge.db",
        beads_dir=tmp_path / "beads",
    )


class TestEdgeBank:
    """Bead storage and retrieval."""

    def test_write_bead_creates_markdown(self, bank, tmp_path):
        """Writing a bead creates a markdown file in beads/."""
        bead = Bead(
            bead_type="entry",
            token_mint="BOAR111",
            token_symbol="BOAR",
            direction="buy",
            amount_sol=0.5,
            price_usd=0.001234,
            thesis="Whale accumulation + narrative convergence",
            signals=["oracle:4_wallets", "narrative:5x_volume"],
            market_conditions="bullish, SOL at $180",
        )
        bead_id = bank.write_bead(bead)

        assert bead_id
        bead_files = list((tmp_path / "beads").glob("*.md"))
        assert len(bead_files) == 1
        content = bead_files[0].read_text()
        assert "BOAR" in content
        assert "Whale accumulation" in content

    def test_write_bead_stores_in_db(self, bank):
        """Writing a bead stores it in SQLite."""
        bead = Bead(
            bead_type="exit",
            token_mint="BOAR111",
            token_symbol="BOAR",
            direction="sell",
            amount_sol=0.75,
            price_usd=0.002468,
            thesis="Take profit at 2x",
            outcome="win",
            pnl_pct=100.0,
            exit_reason="Take profit target hit",
        )
        bank.write_bead(bead)

        stats = bank.get_stats()
        assert stats["total_beads"] == 1
        assert stats["wins"] == 1

    def test_multiple_beads_tracked(self, bank):
        """Multiple beads are stored and counted correctly."""
        for i in range(5):
            import time
            time.sleep(0.01)  # Ensure unique timestamps
            bead = Bead(
                bead_type="entry",
                token_symbol=f"TK{i}",
                outcome="win" if i % 2 == 0 else "loss",
            )
            bank.write_bead(bead)

        stats = bank.get_stats()
        assert stats["total_beads"] == 5
        assert stats["wins"] == 3
        assert stats["losses"] == 2

    def test_query_returns_results(self, bank):
        """Querying with context returns matching beads."""
        # Write some beads first
        bead1 = Bead(
            bead_type="entry",
            token_symbol="WHALE",
            thesis="Whale accumulation detected",
            signals=["oracle:5_wallets"],
            outcome="win",
            pnl_pct=150.0,
        )
        bank.write_bead(bead1)

        import time
        time.sleep(0.01)

        bead2 = Bead(
            bead_type="entry",
            token_symbol="RUG",
            thesis="New pool, high volume",
            signals=["narrative:high_volume"],
            outcome="loss",
            pnl_pct=-100.0,
        )
        bank.write_bead(bead2)

        # Query
        matches = bank.query_similar("whale accumulation pattern", top_k=3)
        assert len(matches) >= 1
        # Should return results (even without embeddings, falls back to recent)
        assert matches[0]["token_symbol"] in ("WHALE", "RUG")

    def test_empty_bank_query(self, bank):
        """Querying an empty bank returns empty list."""
        matches = bank.query_similar("anything")
        assert matches == []

    def test_bead_text_representation(self):
        """Bead.to_text() produces a searchable string."""
        bead = Bead(
            bead_type="entry",
            direction="buy",
            token_symbol="BOAR",
            thesis="Smart money convergence",
            signals=["oracle:4_wallets", "narrative:3x"],
            outcome="pending",
        )
        text = bead.to_text()
        assert "BOAR" in text
        assert "Smart money convergence" in text
        assert "oracle:4_wallets" in text
