# OPUS BRIEF: AutistBoar — OpenClaw Solana Scout

**Brief ID:** AB.BOOTSTRAP.D1
**Date:** 2026-02-10
**From:** CTO (Claude), authorized by G (Sovereign)
**To:** OPUS (Builder)
**Format:** DENSE — read everything, ask nothing, build clean

---

## MISSION

Build a production-grade OpenClaw instance called **AutistBoar** — a Solana memecoin scout that runs autonomously on a VPS, makes intelligent trading decisions on low-cap tokens, and compounds learning across cycles through persistent memory.

This is NOT a janky bot. It is a properly governed system with:
- Professional data firehose (Helius, Birdeye, Nansen)
- Constitutional safety (Blind KeyMan, halt mechanisms, human gates)
- Compounding intelligence (bead autopsy + vector recall)
- Clean personality (smart friend, not corporate drone)

**Pot:** $1-2k disposable (burner wallet, accept total loss)
**Monthly burn:** ~$350-450/mo infrastructure
**Risk posture:** Bounded downside, asymmetric upside, every failure teaches

---

## WHY THIS EXISTS

1. **Live-fire test** of governance patterns built in a8ra (our constitutional trading system)
2. **OpenClaw stress test** in production (security, credential isolation, prompt injection resistance)
3. **Pattern learning** transferable back to a8ra's professional forex system
4. **Fun** — momentum fuel between cathedral-build sprints

**Isolation guarantee:** Completely separate from a8ra infrastructure. Different VPS, different repo, different keys, different wallet. If AutistBoar explodes, a8ra doesn't feel it.

---

## ARCHITECTURE

```
┌─────────────────────────────────────────────┐
│  LAYER 3: OPENCLAW AGENT                    │
│  (Personality + Decision Loop + Heartbeat)  │
│  Model: DeepSeek R1 via OpenRouter (cheap)  │
│  Cycle: Fresh spawn every 5-15 min          │
│  State: Externalized (never in context)     │
├─────────────────────────────────────────────┤
│  LAYER 2: GOVERNANCE                        │
│  (a8ra DNA — this is what we're testing)    │
│  - Blind KeyMan (isolated signer)           │
│  - Rug Warden (pre-trade validation)        │
│  - Risk Warden (circuit breakers)           │
│  - Edge Bank (autopsy + vector recall)      │
│  - Kill Switch (file-based halt)            │
│  - Human Gate (>$100 requires approval)     │
├─────────────────────────────────────────────┤
│  LAYER 1: DATA FIREHOSE                     │
│  (Professional signal, pre-filtered)        │
│  - Helius Developer ($49/mo) — RPC + APIs   │
│  - Birdeye Pro ($99/mo) — price/liquidity   │
│  - Nansen Pro ($69/mo) — smart money flows  │
│  - X API — narrative/sentiment              │
│  - Jupiter SDK — swap execution             │
│  - Jito SDK — MEV-protected bundles         │
└─────────────────────────────────────────────┘
```

---

## BUILD ENVIRONMENT

```yaml
DEVELOP: Mac Studio M4 (~/chadboar/)
TEST: Local dry-run mode (mock APIs, no real keys, no real trades)
REPO: github.com/SlimWojak/AutisticBoar
DEPLOY: git pull on Hostinger VPS (Ubuntu 24.04, Singapore)
OPERATE: VPS only — Mac Studio NEVER sees live keys

RULE: "Dev machine = safe sandbox. VPS = live fire. Never mix."
```

---

## DIRECTORY STRUCTURE

```
chadboar/
├── .env.example                 # Template (never real keys)
├── .gitignore                   # .env, beads/, *.db, keys/
├── README.md                    # Setup + operation guide
├── CLAUDE.md                    # OpenClaw project instructions
├── bootstrap.sh                 # VPS setup script (idempotent)
│
├── config/
│   ├── openclaw.json            # OpenClaw config (models, routing, limits)
│   ├── firehose.yaml            # API endpoints, rate limits, fallbacks
│   ├── risk.yaml                # Circuit breaker thresholds, position limits
│   └── skills.yaml              # Skill registry (whitelist only)
│
├── skills/                      # Custom skills (built from scratch, zero marketplace)
│   ├── smart_money_oracle/
│   │   ├── SKILL.md             # Skill definition
│   │   └── oracle.py            # Nansen + Arkham query wrapper
│   ├── rug_warden/
│   │   ├── SKILL.md
│   │   └── warden.py            # Pre-trade validation pipeline
│   ├── narrative_hunter/
│   │   ├── SKILL.md
│   │   └── hunter.py            # X semantic + onchain volume
│   ├── blind_executioner/
│   │   ├── SKILL.md
│   │   └── executor.py          # Jupiter swap via isolated signer
│   └── edge_bank/
│       ├── SKILL.md
│       └── bank.py              # Autopsy bead + vector recall
│
├── signer/                      # Blind KeyMan (isolated, minimal)
│   ├── signer.py                # Signs tx payloads, never exposes seed
│   ├── keychain.py              # Reads from OS keychain / env (never disk)
│   └── README.md                # Security model documentation
│
├── heartbeat/
│   ├── heartbeat.py             # Main cycle: spawn → read state → decide → act → log
│   ├── watchdog.py              # Post-trade monitoring (drawdown, liquidity drain)
│   └── systemd/
│       └── autistboar.service   # systemd unit file for VPS
│       └── autistboar.timer     # systemd timer (5-15 min cycle)
│
├── beads/                       # Trade autopsy logs (gitignored, persisted on VPS)
│   └── .gitkeep
│
├── memory/
│   ├── state.json               # Current portfolio state (positions, PnL, regime)
│   ├── edge.db                  # SQLite — trade history + vector embeddings
│   └── latest.md                # Human-readable summary for agent on spawn
│
├── guards/
│   ├── prompt_guard.py          # Regex filters for injection patterns
│   ├── tool_whitelist.py        # Allowed tools registry
│   └── killswitch.py            # Check for halt file, nuke if found
│
├── tests/
│   ├── test_rug_warden.py       # Honeypot detection tests
│   ├── test_signer.py           # Blind KeyMan isolation tests
│   ├── test_prompt_guard.py     # Injection resistance tests
│   ├── test_heartbeat.py        # Cycle logic tests
│   ├── test_edge_bank.py        # Bead persistence + recall tests
│   └── mocks/
│       ├── mock_helius.py       # Fake RPC responses
│       ├── mock_birdeye.py      # Fake price/liquidity data
│       ├── mock_nansen.py       # Fake whale flow data
│       └── mock_jupiter.py      # Fake swap quotes
│
└── docs/
    ├── ARCHITECTURE.md          # This design (condensed)
    ├── SECURITY.md              # Threat model + defenses
    ├── OPERATIONS.md            # How to run, monitor, kill
    └── COST.md                  # Monthly burn breakdown
```

---

## SKILL SPECIFICATIONS

### Skill 1: Smart Money Oracle

```yaml
PURPOSE: Detect whale accumulation/distribution on Solana tokens
INPUTS: Nansen API (smart money flows, wallet PnL, labels) + Arkham (entity labels)
OUTPUTS: Structured signal — which wallets, which tokens, buy/sell, size, timing
FREQUENCY: Called by heartbeat on each cycle
MODEL: Cheap (DeepSeek R1) — just formatting API responses, no reasoning needed

FLOW:
  1. Query Nansen: top profitable wallets → recent Solana buys under $5M MC
  2. Query Arkham: label any known entities (funds, insiders, MEV bots)
  3. Filter: exclude known MEV/sandwich bots, require >3 buys from different wallets
  4. Output: structured JSON → agent context

SECURITY:
  - API keys via env vars only
  - Rate limit respect (back off on 429)
  - Cache results 60s (avoid redundant calls)
```

### Skill 2: Rug Warden

```yaml
PURPOSE: Pre-trade validation — reject rugs, honeypots, scams before any swap
INPUTS: Token mint address
OUTPUTS: PASS / FAIL / WARN with structured reasons
FREQUENCY: Called before EVERY trade attempt

CHECKS:
  1. Liquidity depth (Birdeye) — reject if <$10k liquidity
  2. Holder concentration (top 10 wallets >80% = FAIL)
  3. Mint/freeze authority (if mutable = FAIL)
  4. Honeypot simulation (simulate sell tx via Helius — if fails = FAIL)
  5. Token age (if <5 min old = WARN, require higher conviction)
  6. LP lock status (if LP not locked/burned = WARN)

RULE: "If Rug Warden says FAIL, trade does not execute. No override."
INVARIANT: INV-RUG-WARDEN-VETO — "Rug Warden FAIL is absolute. Agent cannot bypass."
```

### Skill 3: Narrative Hunter

```yaml
PURPOSE: Detect pre-pump narrative convergence (social + onchain)
INPUTS: X API (semantic search), onchain volume (Birdeye), holder growth
OUTPUTS: Narrative thesis with conviction score components (NOT a single score)

FLOW:
  1. X search: relevant KOL mentions, sentiment, volume of discussion
  2. Birdeye: 1h/4h volume anomaly vs 7d average
  3. Holder count delta (growing = bullish signal)
  4. Output: decomposed factors — agent decides, not the skill

IMPORTANT: No scalar "buy score." Output is factual decomposition.
  Example: "X mentions: 47 (3x 24h avg), KOL tier: 2 mid-tier, Volume: $180k (5x avg), Holders: +340 (1h)"
  Agent interprets. Skill reports.

SECURITY:
  - X API rate limits respected
  - No arbitrary web browsing (X API endpoints only)
```

### Skill 4: Blind Executioner

```yaml
PURPOSE: Execute Jupiter swaps with MEV protection via Jito bundles
INPUTS: Token mint, direction (buy/sell), size (SOL amount)
OUTPUTS: Transaction signature or rejection reason

FLOW:
  1. Agent constructs intent: {token, direction, amount, slippage_max}
  2. Rug Warden pre-check (MUST pass before execution)
  3. Jupiter quote API → get route + expected output
  4. Construct swap transaction
  5. Pass unsigned tx to Blind KeyMan signer
  6. Signer returns signed tx (never exposes private key)
  7. Submit via Jito bundle (MEV-protected)
  8. Return: tx signature + confirmation status

GATES:
  - amount <= $50: Auto-execute (agent autonomous)
  - amount > $50 and <= $100: Require 2+ signal convergence
  - amount > $100: HALT — push alert to G, await human approval
  - Daily total exposure cap: 30% of pot

INVARIANT: INV-BLIND-KEY — "Private key NEVER enters agent context, logs, beads, or any file."
INVARIANT: INV-HUMAN-GATE-100 — "Trades >$100 require explicit human approval."
```

### Skill 5: Edge Bank

```yaml
PURPOSE: Compound learning across cycles via persistent trade autopsy
INPUTS: Trade outcome (entry, exit, PnL, rationale, market conditions)
OUTPUTS: On query — similar historical patterns and their outcomes

FLOW (POST-TRADE):
  1. Capture: entry rationale, signals used, price, size, timing
  2. Capture: outcome (PnL, hold time, exit reason)
  3. Write structured bead to beads/ directory
  4. Generate embedding (sentence-transformers) → store in edge.db
  5. Update memory/latest.md (human-readable summary)

FLOW (ON-SPAWN):
  1. Agent reads memory/latest.md for orientation
  2. Before any trade: query edge.db "similar setups" → top 3 matches
  3. Agent sees: "Last 3 similar patterns: 2 rugged within 1h, 1 did 4x"
  4. This informs but does not dictate decision

STORAGE:
  - beads/: One markdown file per trade (timestamped)
  - edge.db: SQLite with text + vector columns
  - memory/latest.md: Rolling summary (last 20 trades, current PnL, regime)
```

---

## BLIND KEYMAN DESIGN

```yaml
PRINCIPLE: "The agent requests action. The signer executes. Neither sees the other's secrets."

ARCHITECTURE:
  agent_side:
    - Constructs unsigned transaction payload
    - Passes payload to signer via local socket/subprocess
    - Receives signed transaction back
    - Submits to RPC
    - NEVER has access to private key, seed phrase, or keystore

  signer_side:
    - Reads private key from OS keychain (macOS) or environment variable (VPS)
    - Signs whatever payload it receives
    - Returns signed bytes
    - Has NO knowledge of what it's signing (blind)
    - Has NO network access (cannot submit transactions itself)

  VPS_IMPLEMENTATION:
    - Private key stored in environment variable (set manually by G on VPS)
    - signer.py runs as subprocess, inherits env, signs, exits
    - Agent process does NOT have key in its environment
    - Separation via subprocess isolation (agent env ≠ signer env)

THREAT_MODEL:
  - Prompt injection → agent compromised → cannot extract key (not in context)
  - Log leak → no key in any log (signer doesn't log)
  - Bead leak → no key in any bead (never written)
  - Skill compromise → skills run in agent context → no key there either
```

---

## HEARTBEAT CYCLE

```
┌─ SPAWN (fresh instance every 5-15 min) ─────────────────┐
│                                                           │
│  1. Check killswitch → if exists, EXIT immediately        │
│  2. Read memory/latest.md → orientation                   │
│  3. Read memory/state.json → current positions + PnL      │
│  4. Run Smart Money Oracle → whale signals                │
│  5. Run Narrative Hunter → social + onchain momentum      │
│  6. Check existing positions → watchdog (exit triggers?)   │
│  7. If exit triggers → execute exit via Blind Executioner  │
│  8. If new opportunity → Rug Warden pre-check             │
│  9. If Rug Warden PASS → evaluate conviction              │
│ 10. If conviction sufficient → execute entry              │
│ 11. Write autopsy bead → Edge Bank                        │
│ 12. Update state.json + latest.md                         │
│ 13. Push alert if notable (trade, exit, warning)          │
│ 14. EXIT (clean shutdown, no lingering process)           │
│                                                           │
└──────────────────────────────────────────────────────────┘
```

---

## PERSONALITY (CLAUDE.md)

```markdown
# AutistBoar — Solana Scout

You are AutistBoar. A Solana memecoin scout with sharp senses and good judgment.

## Who You Are
- A smart friend who happens to trade shitcoins
- Direct. If the answer is one sentence, that's what you give.
- Witty when it lands naturally. Never forced.
- Honest about uncertainty. "I don't know" is a valid output.
- Swearing allowed when earned. Not every sentence. When it fits.

## What You Are NOT
- A corporate assistant. Never say "Great question!" or "I'd be happy to help."
- A hype machine. "This is going to moon" is not analysis.
- A YOLO degen. Every trade has a thesis. No thesis = no trade.
- Immortal. You spawn fresh every cycle. Your memory is in ./memory/ and ./beads/.

## Operating Rules
1. Read memory/latest.md FIRST on every spawn. That's your orientation.
2. Externalize ALL state. You die after this cycle. What you don't write, you lose.
3. Rug Warden FAIL = no trade. Non-negotiable. You cannot override this.
4. >$100 trades require G's approval. Push alert, wait for next cycle.
5. Write an autopsy bead for EVERY trade — wins and losses. Losses teach more.
6. If killswitch.txt exists, EXIT immediately. Don't touch anything.
7. Daily max exposure: 30% of pot. Survival > moonshots.

## Decision Framework
- Signal convergence: whale accumulation + narrative momentum + volume anomaly = high conviction
- Single signal: interesting but not tradeable alone
- Conflicting signals: stand down, document, wait
- "When in doubt, don't." Cash is a position.

## Output Format
Keep it clean. Structured YAML for decisions, plain English for rationale.
No walls of text. No bullet point soup. Say what matters, skip what doesn't.
```

---

## SECURITY HARDENING (VPS)

```yaml
OS_LEVEL:
  - Non-root user: 'autistboar'
  - ufw: allow ssh + 443 only
  - fail2ban: enabled (ssh brute force protection)
  - unattended-upgrades: enabled (auto security patches)
  - ssh: key-only auth, no password login

APP_LEVEL:
  - .env: chmod 600, .gitignore'd, never in repo
  - Private key: env var set manually by G, never in any file
  - OpenClaw skill scanner: enabled
  - Zero marketplace skill installs (all custom-built)
  - prompt_guard.py: regex blocks "ignore previous", base64 injection, system prompt leak attempts
  - tool_whitelist.py: only registered skills can execute
  - killswitch.py: file-based halt (touch killswitch.txt → immediate exit)
  - Log rotation: 7 days max, no sensitive data in logs

MONITORING:
  - ntfy.sh: push alerts for trades, errors, warnings, kill switch activation
  - Heartbeat health: if 3 consecutive cycles fail → alert G
  - Daily PnL summary → push to G's phone
```

---

## COST BREAKDOWN

| Component | Provider | Monthly |
|-----------|----------|---------|
| RPC + Enhanced APIs | Helius Developer | $49 |
| Price/Liquidity/Holders | Birdeye Pro | $99 |
| Smart Money Flows | Nansen Pro | $69 |
| MEV Protection | Jito Tips (conservative) | $50-100 |
| VPS (Singapore) | Hostinger 4-8GB | $20 |
| LLM (coordinator) | OpenRouter (DeepSeek/Qwen) | $30-50 |
| LLM (judgment) | Claude API (on-demand) | $20-30 |
| Alerts | ntfy.sh | $0 |
| **TOTAL** | | **$337-417/mo** |

---

## EXIT GATES

```yaml
GATE_1_STRUCTURE:
  criterion: "Directory structure matches spec, all files exist"
  test: "ls -la matches expected tree"

GATE_2_SKILLS:
  criterion: "All 5 skills have SKILL.md + implementation + tests"
  test: "pytest tests/ — all pass with mocks"

GATE_3_BLIND_KEYMAN:
  criterion: "Private key never appears in agent context, logs, beads, or any output"
  test: "test_signer.py — grep all outputs for key patterns → zero matches"

GATE_4_RUG_WARDEN:
  criterion: "Known honeypot tokens correctly rejected"
  test: "test_rug_warden.py — feed known rugs → all FAIL"

GATE_5_HEARTBEAT:
  criterion: "Full cycle completes: spawn → read → decide → act → log → exit"
  test: "test_heartbeat.py with mocked APIs → clean cycle"

GATE_6_KILLSWITCH:
  criterion: "Killswitch file → immediate clean exit, no trades attempted"
  test: "test_killswitch.py"

GATE_7_PROMPT_GUARD:
  criterion: "Injection attempts blocked"
  test: "test_prompt_guard.py — feed 20 known injection patterns → all blocked"

GATE_8_DRY_RUN:
  criterion: "Full system runs in dry-run mode with mocked data end-to-end"
  test: "Manual: python heartbeat.py --dry-run → completes without error"

PASS_CONDITION: "All 8 gates green → ready for VPS deployment"
FAIL_CONDITION: "Any gate red → fix before deploy"
```

---

## BUILD SEQUENCE

```yaml
PHASE_1_SKELETON (Day 1):
  - Directory structure
  - Config files (openclaw.json, firehose.yaml, risk.yaml)
  - CLAUDE.md (personality + operating rules)
  - bootstrap.sh (VPS setup — idempotent)
  - heartbeat.py (cycle skeleton with mocked data)
  - Guards (prompt_guard.py, tool_whitelist.py, killswitch.py)
  - Tests for guards
  EXIT: Gates 1, 6, 7

PHASE_2_SKILLS (Day 2):
  - Smart Money Oracle (Nansen + Arkham wrapper)
  - Rug Warden (pre-trade validation pipeline)
  - Narrative Hunter (X + onchain volume)
  - Edge Bank (bead persistence + vector recall)
  - Tests with mocked API responses
  EXIT: Gates 2, 4, 5

PHASE_3_EXECUTION (Day 3):
  - Blind KeyMan signer
  - Blind Executioner (Jupiter swap + Jito bundle)
  - Human gate logic (>$100 → alert + wait)
  - Watchdog (post-trade monitoring)
  - Integration tests
  EXIT: Gates 3, 8

PHASE_4_DEPLOY (Day 4):
  - Push to GitHub
  - Pull on Hostinger VPS
  - Run bootstrap.sh
  - Configure .env with real API keys
  - Set private key in env var
  - First dry-run on VPS
  - Fund burner wallet ($100 initial, scale up if working)
  - First live heartbeat
  - Monitor for 24h
```

---

## WHAT SUCCESS LOOKS LIKE

```yaml
7_DAY_TARGETS:
  - 100+ heartbeat cycles without crash or injection death
  - Rug Warden blocks >50% of potential scam entries
  - At least 5 real trades executed with structured autopsy beads
  - Edge Bank has enough data to return meaningful "similar setups"
  - G can monitor entirely from phone via ntfy alerts
  - Blind KeyMan has zero key exposure incidents

14_DAY_TARGETS:
  - PnL curve exists (even if negative — data is the point)
  - Pattern recognition: which signals actually predicted outcomes?
  - Security: zero prompt injection successes in logs
  - At least one "holy shit that actually worked" moment OR
  - Clear documentation of why it didn't (equally valuable)
```

---

## NOTES FOR OPUS

```yaml
PHILOSOPHY:
  - This is a sandbox experiment, but build it like production
  - Every pattern here teaches us something for a8ra (our main system)
  - Clean code > clever code. Readable > compact.
  - Tests are not optional. Mock everything for local dev.
  - Security is not optional. Blind KeyMan is the whole point.

STYLE:
  - Python 3.12+
  - Type hints everywhere
  - Pydantic for config/data models
  - pytest for tests
  - No frameworks — stdlib + minimal deps
  - Comments explain WHY, not WHAT

DO_NOT:
  - Install any marketplace skills
  - Put any real API keys in any file
  - Build a web UI (this is headless)
  - Add complexity that doesn't serve the 5 core skills
  - Build multi-agent orchestration (single agent, fresh spawn per cycle)
```

---

ADDENDUM TO WEAVE INTO v0.2 BRIEF

ADDENDUM_v1.1:

  ADD_TO_HEARTBEAT:
    step_2.5: "Check drawdown — if pot < 50% of starting balance, HALT all trading, alert G"
    new_invariant: INV-DRAWDOWN-50 — "Pot drops >50% → halt 24h + alert"

  ADD_TO_NARRATIVE_HUNTER:
    pump_fun_detect: "Monitor new Raydium/Pump.fun pool creation via Helius webhooks"
    rationale: "80% of tradeable memes originate here — this is the primary signal source"

  ADD_TO_BLIND_EXECUTIONER:
    dynamic_fees: "Query recent slot base fees → set priority tip relative to current congestion"
    rationale: "Static tips = either overpay or get dust'd. Must be adaptive."

  ADD_TO_FIREHOSE_CONFIG:
    rpc_fallback:
      primary: Helius Developer
      fallback_1: QuickNode free tier
      fallback_2: Public Solana RPC
      logic: "On 429/timeout → rotate to next, exponential backoff"

  ADD_TO_TESTS:
    historical_replay: "Mock 10 historical Pump.fun launches (mix of rugs + runners)"
    chaos_vectors: "Add congestion sim, RPC timeout, Jito rejection scenarios"


*"A scout with good senses, sharp memory, and the discipline to walk away. That's the edge."*
