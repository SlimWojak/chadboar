"""Bead emitters — helper functions that build and write typed beads.

Each emitter:
  1. Constructs the proper content model with full field population
  2. Sets temporal class, world time, lineage correctly
  3. Calls chain.write_bead() wrapped in try/except (never blocks pipeline)
  4. Returns bead_id on success, empty string on failure

Used by heartbeat_runner.py and paper_trade.py to emit beads at the
correct pipeline stages per BEAD_FIELD_SPEC v0.2.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from lib.beads.schema import (
    AutopsyContent,
    BeadBase,
    BeadType,
    ClaimContent,
    FactContent,
    HeartbeatContent,
    ModelVersionContent,
    PolicyContent,
    ProposalContent,
    ProposalRejectedContent,
    RejectionCategory,
    SignalContent,
    SourceRef,
    SourceType,
    TemporalClass,
)
from lib.beads.chain import BeadChain

log = logging.getLogger("beads.emitters")

AGENT_SOURCE = SourceRef(
    source_type=SourceType.AGENT,
    source_id="chadboar-v0.2",
)


def _safe_write(chain: BeadChain | None, bead: BeadBase) -> str:
    """Write bead to chain, never raise. Returns bead_id or empty string."""
    if chain is None:
        return ""
    try:
        return chain.write_bead(bead)
    except Exception as e:
        log.warning("Bead write failed (%s): %s", bead.bead_type.value, e)
        return ""


# ── FACT ─────────────────────────────────────────────────────────────

def emit_fact_bead(
    chain: BeadChain | None,
    *,
    provider: str,
    field: str,
    value: Any,
    cycle_start: datetime,
    cycle_end: datetime,
    source_status: str = "OK",
) -> str:
    """Emit a FACT bead summarizing one data source's output for this cycle.

    One FACT per source per cycle (not per token — too noisy).
    """
    bead = BeadBase.create(
        bead_type=BeadType.FACT,
        temporal_class=TemporalClass.OBSERVATION,
        source_ref=SourceRef(
            source_type=SourceType.MARKET_DATA,
            source_id=provider,
        ),
        content_model=FactContent(
            symbol="MARKET",
            field=field,
            value=value if isinstance(value, (float, str)) else value,
            as_of_world_time=cycle_end,
            provider=provider,
        ),
        world_time_valid_from=cycle_start,
        world_time_valid_to=cycle_end,
        tags=[f"source:{provider}", f"status:{source_status}"],
    )
    return _safe_write(chain, bead)


# ── CLAIM ────────────────────────────────────────────────────────────

def emit_claim_bead(
    chain: BeadChain | None,
    *,
    conclusion: str,
    reasoning_trace: str,
    confidence_basis: str,
    domain: str,
    premises_ref: list[str] | None = None,
    tokens_referenced: list[str] | None = None,
    cycle_start: datetime,
    cycle_end: datetime,
) -> str:
    """Emit a CLAIM bead — agent's intermediate inference.

    Conditional: only when the agent explicitly commits an assessment
    (e.g., regime-level call, market condition evaluation).
    """
    bead = BeadBase.create(
        bead_type=BeadType.CLAIM,
        temporal_class=TemporalClass.OBSERVATION,
        source_ref=AGENT_SOURCE,
        content_model=ClaimContent(
            conclusion=conclusion,
            reasoning_trace=reasoning_trace,
            premises_ref=premises_ref or [],
            confidence_basis=confidence_basis,
            domain=domain,
            tokens_referenced=tokens_referenced or [],
        ),
        lineage=premises_ref or [],
        world_time_valid_from=cycle_start,
        world_time_valid_to=cycle_end,
        tags=[f"domain:{domain}"],
    )
    return _safe_write(chain, bead)


# ── SIGNAL ───────────────────────────────────────────────────────────

def emit_signal_bead(
    chain: BeadChain | None,
    *,
    token_mint: str,
    token_symbol: str,
    play_type: str,
    discovery_source: str,
    scoring_breakdown: dict,
    conviction_score: int,
    warden_verdict: str,
    red_flags: dict | None = None,
    raw_metrics: dict | None = None,
    risk_profile: dict | None = None,
    fact_bead_ids: list[str] | None = None,
    claim_bead_ids: list[str] | None = None,
) -> str:
    """Emit a SIGNAL bead — scored candidate with full conviction breakdown.

    One SIGNAL per scored candidate (replaces old VERDICT bead type).
    Lineage points to FACT beads from this cycle's data sources.
    """
    lineage = []
    supporting_facts = fact_bead_ids or []
    supporting_claims = claim_bead_ids or []
    lineage.extend(supporting_facts)
    lineage.extend(supporting_claims)

    if not lineage:
        lineage = ["no_fact_beads_this_cycle"]

    bead = BeadBase.create(
        bead_type=BeadType.SIGNAL,
        temporal_class=TemporalClass.DERIVED,
        source_ref=AGENT_SOURCE,
        content_model=SignalContent(
            token_mint=token_mint,
            token_symbol=token_symbol,
            play_type=play_type,
            discovery_source=discovery_source,
            scoring_breakdown=scoring_breakdown,
            conviction_score=min(max(conviction_score, 0), 100),
            warden_verdict=warden_verdict,
            red_flags=red_flags or {},
            raw_metrics=raw_metrics or {},
            risk_profile=risk_profile or {},
            supporting_facts=supporting_facts,
            supporting_claims=supporting_claims,
        ),
        lineage=lineage,
        tags=[f"token:{token_symbol}", f"play:{play_type}"],
    )
    return _safe_write(chain, bead)


# ── PROPOSAL ─────────────────────────────────────────────────────────

def emit_proposal_bead(
    chain: BeadChain | None,
    *,
    signal_bead_id: str,
    action: str,
    token_mint: str,
    token_symbol: str,
    entry_price_fdv: float | None = None,
    position_size_sol: float | None = None,
    execution_venue: str = "paper",
    gate: str = "auto",
    stop_loss: dict | None = None,
    constraints: list[str] | None = None,
) -> str:
    """Emit a PROPOSAL bead — trade intent (paper or live).

    Emitted for PAPER_TRADE, WATCHLIST, and AUTO_EXECUTE recommendations.
    """
    bead = BeadBase.create(
        bead_type=BeadType.PROPOSAL,
        temporal_class=TemporalClass.DERIVED,
        source_ref=AGENT_SOURCE,
        content_model=ProposalContent(
            signal_ref=signal_bead_id,
            action=action,
            token_mint=token_mint,
            token_symbol=token_symbol,
            entry_price_fdv=entry_price_fdv,
            position_size_sol=position_size_sol,
            execution_venue=execution_venue,
            gate=gate,
            stop_loss=stop_loss,
            constraints=constraints or [],
        ),
        lineage=[signal_bead_id] if signal_bead_id else [],
        tags=[f"token:{token_symbol}", f"venue:{execution_venue}"],
    )
    return _safe_write(chain, bead)


# ── PROPOSAL_REJECTED ────────────────────────────────────────────────

def emit_proposal_rejected_bead(
    chain: BeadChain | None,
    *,
    signal_bead_id: str,
    token_mint: str,
    token_symbol: str,
    rejection_source: str,
    rejection_reason: str,
    rejection_category: RejectionCategory,
    gate: str = "auto",
    scoring_breakdown: dict | None = None,
    warden_detail: dict | None = None,
    risk_metrics: dict | None = None,
    policy_ref: str | None = None,
) -> str:
    """Emit a PROPOSAL_REJECTED bead — full signal snapshot + rejection context.

    Emitted for VETO and DISCARD recommendations. This IS the Shadow Field.
    """
    bead = BeadBase.create(
        bead_type=BeadType.PROPOSAL_REJECTED,
        temporal_class=TemporalClass.DERIVED,
        source_ref=AGENT_SOURCE,
        content_model=ProposalRejectedContent(
            signal_ref=signal_bead_id,
            action="ENTER_LONG",
            token_mint=token_mint,
            token_symbol=token_symbol,
            execution_venue="paper",
            gate=gate,
            rejection_source=rejection_source,
            rejection_reason=rejection_reason,
            rejection_category=rejection_category,
            rejection_policy_ref=policy_ref,
            scoring_breakdown_at_rejection=scoring_breakdown or {},
            warden_detail=warden_detail,
            risk_metrics_at_rejection=risk_metrics or {},
        ),
        lineage=[signal_bead_id] if signal_bead_id else [],
        tags=[f"token:{token_symbol}", f"rejected:{rejection_category.value}"],
    )
    return _safe_write(chain, bead)


# ── HEARTBEAT ────────────────────────────────────────────────────────

def emit_heartbeat_bead(
    chain: BeadChain | None,
    *,
    cycle_number: int,
    signals_found: int = 0,
    signals_vetoed: int = 0,
    proposals_emitted: int = 0,
    pot_sol: float = 0.0,
    positions_count: int = 0,
    pipeline_health: dict | None = None,
    canary_hash: str = "",
    previous_heartbeat_id: str | None = None,
    cycle_start: datetime,
    cycle_end: datetime,
) -> str:
    """Emit a HEARTBEAT bead — cycle-level metadata."""
    lineage = [previous_heartbeat_id] if previous_heartbeat_id else []

    bead = BeadBase.create(
        bead_type=BeadType.HEARTBEAT,
        temporal_class=TemporalClass.OBSERVATION,
        source_ref=AGENT_SOURCE,
        content_model=HeartbeatContent(
            cycle_number=cycle_number,
            signals_found=signals_found,
            signals_vetoed=signals_vetoed,
            proposals_emitted=proposals_emitted,
            pot_sol=pot_sol,
            positions_count=positions_count,
            pipeline_health=pipeline_health or {},
            canary_hash=canary_hash,
            previous_heartbeat_id=previous_heartbeat_id,
        ),
        lineage=lineage,
        world_time_valid_from=cycle_start,
        world_time_valid_to=cycle_end,
        tags=[f"cycle:{cycle_number}"],
    )
    return _safe_write(chain, bead)


# ── POLICY ───────────────────────────────────────────────────────────

def emit_policy_bead(
    chain: BeadChain | None,
    *,
    policy_name: str,
    policy_type: str,
    rules: dict,
    authority: str = "system_default",
    supersedes: str | None = None,
) -> str:
    """Emit a POLICY bead — snapshot of risk/execution config.

    Emitted on first heartbeat after restart, or on config change.
    """
    now = datetime.now(timezone.utc)
    bead = BeadBase.create(
        bead_type=BeadType.POLICY,
        temporal_class=TemporalClass.PATTERN,
        source_ref=SourceRef(
            source_type=SourceType.HUMAN,
            source_id=authority,
        ),
        content_model=PolicyContent(
            policy_name=policy_name,
            policy_type=policy_type,
            rules=rules,
            effective_from=now,
            supersedes=supersedes,
            authority=authority,
        ),
        tags=[f"policy:{policy_name}"],
    )
    return _safe_write(chain, bead)


# ── MODEL_VERSION ────────────────────────────────────────────────────

def emit_model_version_bead(
    chain: BeadChain | None,
    *,
    model_name: str,
    version_hash: str,
    purpose: str,
    config_snapshot: dict | None = None,
) -> str:
    """Emit a MODEL_VERSION bead — LLM config at deployment.

    Emitted on startup, or when model changes.
    """
    bead = BeadBase.create(
        bead_type=BeadType.MODEL_VERSION,
        temporal_class=TemporalClass.PATTERN,
        source_ref=AGENT_SOURCE,
        content_model=ModelVersionContent(
            model_name=model_name,
            version_hash=version_hash,
            purpose=purpose,
            deployment_status="PRODUCTION",
            config_snapshot=config_snapshot or {},
        ),
        tags=[f"model:{model_name}"],
    )
    return _safe_write(chain, bead)


# ── AUTOPSY ──────────────────────────────────────────────────────────

def emit_autopsy_bead(
    chain: BeadChain | None,
    *,
    trade_bead_id: str,
    token_mint: str,
    token_symbol: str,
    pnl_sol: float = 0.0,
    pnl_pct: float = 0.0,
    exit_price: float = 0.0,
    exit_reason: str = "",
    hold_duration_seconds: int = 0,
    lesson: str = "",
    supports_thesis: bool | None = None,
) -> str:
    """Emit an AUTOPSY bead — post-trade evaluation.

    ChadBoar extension. Maps to CLAIM with domain="autopsy" in a8ra.
    """
    bead = BeadBase.create(
        bead_type=BeadType.AUTOPSY,
        temporal_class=TemporalClass.DERIVED,
        source_ref=AGENT_SOURCE,
        content_model=AutopsyContent(
            trade_bead_id=trade_bead_id,
            token_mint=token_mint,
            token_symbol=token_symbol,
            pnl_sol=pnl_sol,
            pnl_pct=pnl_pct,
            exit_price=exit_price,
            exit_reason=exit_reason,
            hold_duration_seconds=hold_duration_seconds,
            lesson=lesson,
            supports_thesis=supports_thesis,
        ),
        lineage=[trade_bead_id] if trade_bead_id else [],
        tags=[
            f"token:{token_symbol}",
            f"pnl:{'positive' if pnl_pct > 0 else 'negative'}",
        ],
    )
    return _safe_write(chain, bead)
