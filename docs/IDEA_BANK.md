# Idea Bank

Future enhancements and extensions for AutistBoar and a8ra. Tagged by readiness:
- **NOW** — actionable today, low effort, high value (or already proven in production)
- **AFTER_BEADS** — needs 50+ trade beads or live trading history first
- **A8RA_ONLY** — pattern research for multi-agent architecture, not Boar priority

## Ideas

| # | Date | Idea | Source | Tag |
|---|------|------|--------|-----|
| 1 | 2026-02-10 | Telegram inline buttons for >$100 trade approvals — one-tap approve/reject instead of typed responses | G | AFTER_BEADS — needs first live trade to test gate |
| 2 | 2026-02-10 | Multi-token position heat map in daily digest — visual portfolio exposure by sector/narrative | G | AFTER_BEADS — needs open positions |
| 3 | 2026-02-10 | Weekly autopsy digest — most valuable beads, pattern synthesis, what worked/what didn't | G | AFTER_BEADS — needs beads |
| 4 | 2026-02-11 | Feedback directory (`state/feedback/`) — structured G approvals/rejections that persist into future heartbeat decisions. Closes the loop between Edge Bank and behavioral change. [CP-17] | CTO Claude | AFTER_BEADS — needs trades for G to approve/reject |
| 5 | 2026-02-11 | Roundtable cross-signal synthesis in daily digest — "what cross-signals emerged across all strategies today." Multi-agent curator pattern. [CP-16] | CTO Claude | A8RA_ONLY — multi-strategy prerequisite |
| 6 | 2026-02-11 | Multi-strategy shared context — each strategy writes to own subdirectory, Strategist reads across all for cross-signals | CTO Claude | A8RA_ONLY |
| 7 | 2026-02-11 | ChronoBets prediction market skill — small USDC bets on Pyth oracle markets as conviction calibration | G + CTO Claude | AFTER_BEADS — after core loop validated |
| 8 | 2026-02-10 | Structured reasoning chains at decision time — logic tree with every decision, persisted in bead for human review. Evidence → reasoning → decision → G audits the logic, not the trade. Can prototype on heartbeat cycle decisions, not just trades. Core governance pattern: auditable reasoning. | G | NOW — prototype on heartbeat decisions (a8ra governance pattern) |
| 9 | 2026-02-11 | Tiered bead context loading (L0/L1/L2) — `recent.md`, `monthly_summary.md`, `archive/`. Queries hit recent first. No lossy compression on trade data. | G | AFTER_BEADS — revisit when beads/ > 50 |
| 10 | 2026-02-11 | SkillRL distillation — Opus daily job reads beads, extracts reusable success heuristics + failure avoidance rules, writes to `state/skillbank/`. Heartbeats retrieve skills not beads. The learning flywheel. See detailed notes below. [CP-10] | CTO Claude + SkillRL paper | AFTER_BEADS — needs 50+ beads |
| 11 | 2026-02-12 | Agent role registry — agents need to know who they are, what they can touch, who else is operating. Boar inherited a self-preservation rule that applied to himself but confused an external operator (Opus/Cursor). A8ra needs an agent manifest per role with explicit scope boundaries. | G + Opus | A8RA_ONLY — architecture pattern for multi-agent |
| 12 | 2026-02-12 | Session context collapse detection — cheap models (DeepSeek) latch onto shortest patterns in accumulating sessions. Known failure mode: heartbeat outputs shrink to 5 tokens, gateway marks silent. Fix: nuke session file + restart. Applicable to any agent running recurring loops. Documented in AGENTS.md. **BUILT:** `lib/guards/session_health.py`, wired into HEARTBEAT.md step 1b. | Opus (proven in production) | NOW — BUILT + deployed |
| 13 | 2026-02-12 | systemd over tmux for production agents — supervised processes with auto-restart, journal logging, `EnvironmentFile` for env vars. Proven superior to tmux/screen sessions that die silently. | G + Opus | NOW — already deployed, document as standard |
| 14 | 2026-02-12 | EnvironmentFile with secret isolation — load API keys via systemd `EnvironmentFile` but keep signer private keys OUT of the file entirely. Signer subprocess reads key from its own isolated path. Defence in depth for INV-BLIND-KEY. | Opus | NOW — already deployed, verify isolation |
| 15 | 2026-02-12 | Zombie process detection — stale gateway from prior era caused Telegram auth failures and API conflicts. Health check verifies only one gateway PID is running. **BUILT:** `lib/guards/zombie_gateway.py`, wired into HEARTBEAT.md step 1a. | Opus (learned from outage) | NOW — BUILT + deployed |
| 16 | 2026-02-12 | Red Team / Challenger agent — adversarial model argues against high-conviction trades before execution. Devil's advocate gate between conviction scoring and trade execution. [CP-11] | v0.2 broadcast | AFTER_BEADS — needs live trading + conviction data |
| 17 | 2026-02-12 | World Surface / War Room — structured projection layer (Linear/Asana style) over canonical state. Invariants: surface reads from state, never writes. State is canonical, surface is derived. [CP-14] | v0.2 broadcast | A8RA_ONLY — needs multi-agent coordination |
| 18 | 2026-02-12 | GPT-5.3 Codex as coding delegate — Sonnet writes spec, Codex implements, Sonnet reviews. Separates architecture from implementation. [CP-15] | v0.2 broadcast | A8RA_ONLY — tooling pattern, not Boar priority |
| 19 | 2026-02-12 | Agent Board (LobeHub) as MCP surface layer experiment. Test whether projecting Boar's candidate pipeline onto an agent-native Kanban (with DAG dependencies and MCP tool exposure) improves agent orientation vs the current filesystem world model. Two-layer architecture: Agent Board as inner surface for agents, Linear as optional outer surface for humans. Invariants: INV-WORLD-AUTHORITY-1 through INV-FAIL-OPEN-SURFACE-1 apply. Ref: Perplexity research doc on world surface options. | G | A8RA_ONLY — world surface experiment, tests orientation pattern |

## Selection Criteria

Ideas move from PARKED → ACTIVE when:
1. Core trading loop has 30+ days of production data
2. Edge Bank has meaningful pattern density (50+ beads)
3. G explicitly prioritizes the enhancement
4. The idea solves a demonstrated pain point (not speculative optimization)

---

## DETAILED NOTES

### Idea #10: SkillRL Pattern — Edge Bank Evolution

**Problem it solves:**  
Edge Bank currently stores raw trade beads (logs). Conviction scoring does vector search for "similar setups" — matching on surface features, not extracted wisdom. This works early but plateaus: more beads = more noise, not more intelligence. The agent re-derives insights from raw data every cycle instead of building on distilled knowledge.

**The SkillRL pattern (3 components):**

1. **DISTILLATION** — Strong model (Opus) periodically reads batches of raw beads and extracts two types of skills:
   - **Success skills:** generalized heuristics from winning trades  
     Example: *"Whale accumulation preceding social momentum by 10-30min has >60% win rate. Whale-first = genuine accumulation. Social-first = manufactured pump."*
   - **Failure skills:** avoidance rules from losing trades  
     Example: *"Avoid tokens where KOL posts precede whale activity. This pattern correlates with pump-and-dump. 4 of 5 such entries resulted in >20% loss."*

2. **HIERARCHICAL SKILLBANK** — Skills organized in two tiers:
   - **General skills:** apply across all tokens/strategies (e.g., "timing of whale vs social signal matters more than absolute volume")
   - **Task-specific skills:** apply to specific token categories or market regimes (e.g., "AI narrative tokens have ~1hr momentum window vs ~4hr for meme tokens")
   - Stored in: `state/skillbank/general.md` + `state/skillbank/specific.md` (or similar structure — implementation detail, not architecture decision)

3. **RECURSIVE CO-EVOLUTION** — The critical loop:
   - **Daily:** Opus reads last N beads → distills new/updated skills → writes to SkillBank
   - **Every heartbeat:** Strategist retrieves relevant SKILLS (not raw beads) during conviction evaluation
   - Better skills → better trades → richer beads → better distillation → repeat
   - The library and the trading performance co-evolve over time

**How it changes the current pipeline:**

**CURRENT:**  
Heartbeat → score opportunity → vector search Edge Bank for similar beads → crude pattern match (10 pts in conviction) → trade decision

**UPGRADED:**  
Heartbeat → score opportunity → retrieve relevant skills from SkillBank → Strategist applies distilled heuristics + avoidance rules → sharper conviction → trade decision

Meanwhile (async, daily):  
Opus reads new beads → distills/updates SkillBank → next day's heartbeats are smarter

**Implementation prerequisites:**
- 50+ real trade beads (success + failure) before first distillation is meaningful
- Opus daily job scheduled (cost: ~$0.10-0.30 per run depending on bead volume)
- SkillBank directory structure created
- Conviction scoring updated to retrieve skills instead of (or in addition to) raw beads
- Edge Bank raw beads preserved for audit — SkillBank is derived layer, not replacement

**Why this matters for a8ra:**  
This is the exact mechanism the Research Lab needs. Research generates raw findings (beads). A distillation layer extracts reusable strategies (skills). The Strategy Office retrieves skills, not raw research. The human gate reviews reasoning chains built on distilled skills — faster review, higher quality decisions. Same pattern, larger scale.

**Reference:**  
SkillRL paper — recursive skill evolution in RL for LLM agents. Key claims: 33% faster convergence, ~20% fewer tokens, higher final success rate vs raw trajectory memory.

---

## Archive

Rejected ideas and reasons go here.
