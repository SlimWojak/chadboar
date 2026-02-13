# BUILD PLAN v0.2 — AutistBoar (OpenClaw Native)

**Version:** 0.2.1
**Date:** 2026-02-10
**Author:** Opus (Builder), from CTO Brief + Addendum v1.1 + OpenClaw docs + G updates
**Status:** APPROVED — executing Session 1

---

## CRITICAL REFRAME: What OpenClaw Gives Us For Free

After reading the OpenClaw docs, GitHub, practitioner guide, and the full
config/skills/heartbeat/cron reference, the scope of what we build shrinks
dramatically. OpenClaw IS the agent runtime.

### We Do NOT Build
- Agent loop (Pi agent runtime handles this)
- Model routing + fallbacks (openclaw.json config)
- Heartbeat mechanism (Gateway heartbeat, configurable interval)
- Cron scheduling (Gateway cron, isolated or main session)
- Memory system (daily memory files + compaction, built-in)
- Session management (per-sender, per-channel sessions)
- Channel connections (Telegram/Discord for G's alerts — built-in)
- Tool execution (bash, read, write, edit, browser — built-in)
- Prompt assembly (AGENTS.md, SOUL.md, TOOLS.md auto-loaded)
- Config hot-reload (Gateway watches files)

### We DO Build
- **AGENTS.md** — AutistBoar operating rules, governance invariants, decision framework
- **SOUL.md** — Persona/tone (smart scout, not corporate drone)
- **HEARTBEAT.md** — The 14-step trading cycle as a heartbeat checklist
- **5 Custom Skills** — SKILL.md + Python scripts that the agent calls via bash
- **Python execution layer** — API clients, signer, guards, edge bank
- **openclaw.json** — Model routing, heartbeat config, channel setup, skill env vars
- **bootstrap.sh** — VPS provisioning + OpenClaw installation
- **Tests** — For our Python execution layer (guards, signer, warden)

---

## DUAL-MODE ARCHITECTURE (v0.2.1 — OpenClaw Native)

AutistBoar is TWO things in one brain:
1. **Autonomous trading scout** — heartbeat cycle, DeepSeek R1, no human in loop
2. **Interactive Telegram assistant** — on-demand, Sonnet, G talks to it directly

Same workspace, same skills, same bead memory. Different triggers, different models.

```
┌─────────────────────────────────────────────────────────┐
│  OPENCLAW GATEWAY (Node.js, always-on)                  │
│                                                          │
│  MODE 1: AUTONOMOUS (Heartbeat — every 10 min)          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Model: DeepSeek R1 via OpenRouter (cheap executor)│  │
│  │  Trigger: Gateway heartbeat timer                  │  │
│  │  Reads: HEARTBEAT.md → runs 14-step trading cycle  │  │
│  │  No personality needed. Pure execution.            │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  MODE 2: INTERACTIVE (Telegram — on demand)              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Model: Sonnet (Claude) — "the face you talk to"   │  │
│  │  Trigger: G sends a Telegram message               │  │
│  │  Personality: smart friend, witty, direct           │  │
│  │  Also formats trade alerts (readable, not raw JSON) │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  SHARED: Skills, beads, state, memory                    │
│  ┌────────────────┐  ┌────────────────────────┐         │
│  │  Telegram (G)   │  │  Cron Jobs             │         │
│  │  - Chat (Sonnet)│  │  - Daily PnL (isolated)│         │
│  │  - Alerts       │  │  - Weekly edge review  │         │
│  └────────────────┘  └────────────────────────┘         │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Skills (workspace/skills/)                        │  │
│  │  smart_money_oracle | rug_warden                   │  │
│  │  narrative_hunter | blind_executioner              │  │
│  │  edge_bank                                         │  │
│  └───────────────┬────────────────────────────────────┘  │
└──────────────────┼──────────────────────────────────────┘
                   │ bash / python
┌──────────────────▼──────────────────────────────────────┐
│  PYTHON EXECUTION LAYER (lib/)                          │
│  ┌─────────────┐  ┌──────────────┐                      │
│  │ API Clients  │  │ Blind KeyMan │                      │
│  │ helius.py    │  │ signer.py    │                      │
│  │ birdeye.py   │  │ keychain.py  │                      │
│  │ nansen.py    │  │              │                      │
│  │ x_api.py     │  └──────────────┘                      │
│  └─────────────┘                                         │
│  ┌─────────────┐  ┌──────────────┐                      │
│  │ Guards       │  │ Edge Bank    │                      │
│  │ drawdown.py  │  │ bank.py      │                      │
│  │ killswitch.py│  │ embeddings   │                      │
│  │ risk.py      │  │ bead writer  │                      │
│  └─────────────┘  └──────────────┘                      │
│  ┌─────────────────────────────────┐                    │
│  │ Execution                       │                    │
│  │ jupiter.py (swap quotes/routes) │                    │
│  │ jito.py    (MEV bundle submit)  │                    │
│  └─────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

**How it works:**
- **Heartbeat** fires every 10 min → DeepSeek R1 reads HEARTBEAT.md → runs the
  14-step trading cycle → calls Python skills via bash → updates state → exits.
  No personality, pure execution. Cheap.
- **Telegram message from G** → Sonnet reads AGENTS.md + SOUL.md → responds as
  the smart scout persona → can use the same skills on demand → formats alerts
  with personality. This is the "pocket assistant" mode.
- **Trade alerts** → Sonnet formats them (witty, readable) and delivers to Telegram.
  Not raw JSON dumps.

Both modes share the same workspace, skills, bead memory, and state files.

---

## WORKSPACE LAYOUT

The OpenClaw workspace IS our project root. We set
`agents.defaults.workspace` to point here.

```
~/autisticboar/                        # OpenClaw workspace root
├── AGENTS.md                          # Operating rules + governance invariants
├── SOUL.md                            # Persona — smart scout, not drone
├── USER.md                            # Who G is, how to address them
├── IDENTITY.md                        # AutistBoar name/emoji
├── TOOLS.md                           # Tool usage notes
├── HEARTBEAT.md                       # Trading cycle checklist (the core loop)
├── .env.example                       # Template (never real keys)
├── .gitignore                         # .env, beads/*, *.db, memory/, state/
├── README.md                          # Setup + operation guide
│
├── skills/                            # OpenClaw skills (SKILL.md per skill)
│   ├── smart_money_oracle/
│   │   └── SKILL.md                   # Instructions: how to query whale data
│   ├── rug_warden/
│   │   └── SKILL.md                   # Instructions: how to validate tokens
│   ├── narrative_hunter/
│   │   └── SKILL.md                   # Instructions: how to scan narratives
│   ├── blind_executioner/
│   │   └── SKILL.md                   # Instructions: how to execute swaps
│   └── edge_bank/
│       └── SKILL.md                   # Instructions: how to write/query beads
│
├── lib/                               # Python execution layer
│   ├── __init__.py
│   ├── clients/                       # API wrappers
│   │   ├── __init__.py
│   │   ├── base.py                    # Shared HTTP: rate limit, retry, backoff
│   │   ├── helius.py                  # RPC + enhanced APIs + fallback chain
│   │   ├── birdeye.py                 # Price, liquidity, holders
│   │   ├── nansen.py                  # Smart money flows
│   │   ├── x_api.py                   # X search wrapper
│   │   ├── jupiter.py                 # Swap quote + route
│   │   └── jito.py                    # MEV-protected bundle submission
│   │
│   ├── skills/                        # Skill entry points (CLI tools)
│   │   ├── oracle_query.py            # CLI: python -m lib.skills.oracle_query
│   │   ├── warden_check.py            # CLI: python -m lib.skills.warden_check
│   │   ├── narrative_scan.py          # CLI: python -m lib.skills.narrative_scan
│   │   ├── execute_swap.py            # CLI: python -m lib.skills.execute_swap
│   │   ├── bead_write.py              # CLI: python -m lib.skills.bead_write
│   │   └── bead_query.py             # CLI: python -m lib.skills.bead_query
│   │
│   ├── signer/                        # Blind KeyMan (isolated process)
│   │   ├── __init__.py
│   │   ├── signer.py                  # Signs tx payloads, never exposes seed
│   │   └── keychain.py                # Reads key from env (VPS) / keychain (mac)
│   │
│   ├── guards/                        # Safety mechanisms
│   │   ├── __init__.py
│   │   ├── killswitch.py              # Check halt file
│   │   ├── drawdown.py                # Pot < 50% starting → halt
│   │   └── risk.py                    # Daily exposure check, position limits
│   │
│   └── edge/                          # Edge Bank storage layer
│       ├── __init__.py
│       ├── bank.py                    # Bead CRUD + vector recall
│       └── embeddings.py              # sentence-transformers wrapper
│
├── config/                            # Our config files (read by Python layer)
│   ├── risk.yaml                      # Circuit breakers, position limits, drawdown
│   └── firehose.yaml                  # API endpoints, rate limits, RPC fallback
│
├── state/                             # Runtime state (gitignored on VPS)
│   ├── state.json                     # Positions, PnL, daily exposure, pot balance
│   └── latest.md                      # Human-readable summary for heartbeat
│
├── beads/                             # Trade autopsy logs (gitignored)
│   └── .gitkeep
│
├── memory/                            # OpenClaw daily memory (auto-managed)
│   └── .gitkeep
│
├── tests/                             # Python tests for our execution layer
│   ├── test_rug_warden.py
│   ├── test_signer.py
│   ├── test_guards.py
│   ├── test_edge_bank.py
│   ├── test_oracle.py
│   ├── test_narrative.py
│   └── mocks/
│       ├── mock_helius.py
│       ├── mock_birdeye.py
│       ├── mock_nansen.py
│       └── mock_jupiter.py
│
├── scripts/
│   └── bootstrap.sh                   # VPS setup (idempotent)
│
└── docs/
    ├── AUTISTBOAR_OPUS_BRIEF.md       # Original brief
    ├── BUILD_PLAN_v0.2.md             # This document
    ├── SECURITY.md                    # Threat model
    └── OPERATIONS.md                  # How to run, monitor, kill
```

### OpenClaw Config (separate from workspace)

```
~/.openclaw/
├── openclaw.json                      # Gateway config (model, heartbeat, channels)
└── .env                               # API keys (Helius, Birdeye, Nansen, OpenRouter, etc.)
```

---

## OPENCLAW CONFIGURATION (openclaw.json)

```json5
{
  // ──────────────────────────────────────────────────
  // MODEL ROUTING (via OpenRouter)
  // All models accessed through single OPENROUTER_API_KEY.
  // Ref: https://openrouter.ai/docs/guides/openclaw-integration
  // ──────────────────────────────────────────────────

  // API key — stored in ~/.openclaw/.env, NOT in this file
  env: {
    OPENROUTER_API_KEY: "${OPENROUTER_API_KEY}",
  },

  agents: {
    defaults: {
      workspace: "~/autisticboar",

      // PRIMARY: Sonnet for interactive Telegram chat (the face G talks to)
      // Fallback chain: if Sonnet is down → DeepSeek → Gemini Flash
      model: {
        primary: "openrouter/anthropic/claude-sonnet-4-5",
        fallbacks: [
          "openrouter/deepseek/deepseek-r1",
          "openrouter/google/gemini-2.5-flash-lite"
        ]
      },

      // Declare all models we reference anywhere in config
      models: {
        "openrouter/anthropic/claude-sonnet-4-5": {},    // Chat personality
        "openrouter/deepseek/deepseek-r1": {},            // Heartbeat executor
        "openrouter/google/gemini-2.5-flash-lite": {},    // Fallback (cheap)
        "openrouter/openrouter/auto": {},                 // Cron jobs (cost-optimized)
      },

      // HEARTBEAT: DeepSeek R1 — emotionless executor for autonomous trading
      // Runs every 10 min, no personality needed, just execution.
      heartbeat: {
        every: "10m",
        model: "openrouter/deepseek/deepseek-r1",
        target: "telegram",
        to: "<G_TELEGRAM_CHAT_ID>",
        activeHours: {
          start: "00:00",
          end: "24:00",     // 24/7 — this is a trading bot
        },
        prompt: "Read HEARTBEAT.md and follow it strictly. Read state/latest.md for orientation. If nothing needs attention, reply HEARTBEAT_OK."
      },

      userTimezone: "Asia/Singapore",

      // Memory compaction — flush trading context to daily files
      compaction: {
        mode: "default",
        memoryFlush: {
          enabled: true,
          softThresholdTokens: 40000,
          prompt: "Distill this session to memory. Focus on trades executed, signals observed, decisions made, and lessons learned. If nothing worth storing: NO_FLUSH"
        }
      },
    }
  },

  // ──────────────────────────────────────────────────
  // TELEGRAM — G's interface to AutistBoar
  // Chat → Sonnet (personality). Heartbeat alerts → Sonnet formats.
  // ──────────────────────────────────────────────────
  channels: {
    telegram: {
      botToken: "${TELEGRAM_BOT_TOKEN}",
      allowFrom: ["<G_TELEGRAM_USER_ID>"],
    }
  },

  // ──────────────────────────────────────────────────
  // SKILLS — custom only, zero marketplace (INV-NO-MARKETPLACE)
  // Environment variables injected per-skill at agent runtime.
  // ──────────────────────────────────────────────────
  skills: {
    entries: {
      "smart-money-oracle": {
        enabled: true,
        env: {
          NANSEN_API_KEY: "${NANSEN_API_KEY}",
        }
      },
      "rug-warden": {
        enabled: true,
        env: {
          HELIUS_API_KEY: "${HELIUS_API_KEY}",
          BIRDEYE_API_KEY: "${BIRDEYE_API_KEY}",
        }
      },
      "narrative-hunter": {
        enabled: true,
        env: {
          X_BEARER_TOKEN: "${X_BEARER_TOKEN}",
          BIRDEYE_API_KEY: "${BIRDEYE_API_KEY}",
        }
      },
      "blind-executioner": {
        enabled: true,
        env: {
          HELIUS_API_KEY: "${HELIUS_API_KEY}",
        }
      },
      "edge-bank": { enabled: true },
    },
    allowBundled: [],  // Disable ALL bundled skills — custom only
  },

  // ──────────────────────────────────────────────────
  // LOGGING — redact tool outputs to prevent key leaks
  // ──────────────────────────────────────────────────
  logging: {
    redactSensitive: "tools",
  },

  // ──────────────────────────────────────────────────
  // CRON — configured post-deploy for scheduled tasks
  // Uses Auto model for cost optimization on routine jobs.
  // ──────────────────────────────────────────────────
  cron: { enabled: true },
}
```

### Model Routing Strategy

All models accessed via a single OpenRouter API key. Browse available models
at [openrouter.ai/models](https://openrouter.ai/models).

| Context | Model | Why | Cost/call |
|---------|-------|-----|-----------|
| G chats on Telegram | Sonnet 4.5 | Personality, wit, readability | ~$0.01-0.05 |
| Heartbeat cycle (10 min) | DeepSeek R1 | Cheap executor, no personality needed | ~$0.002 |
| Trade alert formatting | Sonnet 4.5 | Formats for humans, not raw JSON | ~$0.01 |
| Daily PnL cron | Auto | Cost-optimized, routine summary | ~$0.001 |
| Weekly edge review cron | Sonnet 4.5 | Needs reasoning for pattern analysis | ~$0.03 |
| Fallback (any failure) | Gemini Flash Lite | Ultra-cheap, keeps system alive | ~$0.0005 |

**Why OpenRouter Auto for cron?** The [Auto model](https://openrouter.ai/models/openrouter/auto)
picks the cheapest model that can handle the prompt. Perfect for routine cron jobs
like daily PnL summaries where we don't care which model, just that it's cheap.

**Fallback chain matters.** If Sonnet rate-limits or goes down, the agent
degrades to DeepSeek → Gemini Flash. Trading cycle never stops.

**Future: per-channel routing.** If we later add a dedicated "trading alerts"
Telegram group, we can route it to DeepSeek while keeping G's DM on Sonnet.
OpenClaw supports this natively via channel-level model config.

**Auth profile (VPS).** On deploy, we'll use `openclaw auth set` to store the
OpenRouter key in the system keychain instead of a config file. Better security.

---

## INVARIANTS (Non-Negotiable — All 7)

Encoded in AGENTS.md so they are part of every agent turn's system prompt.

| ID | Rule | Enforcement |
|----|------|-------------|
| INV-BLIND-KEY | Private key NEVER enters agent context, logs, beads, or any file | Subprocess isolation, signer.py |
| INV-RUG-WARDEN-VETO | Rug Warden FAIL = no trade. No override. | Hard gate in HEARTBEAT.md cycle |
| INV-HUMAN-GATE-100 | Trades >$100 require G's approval via Telegram | Alert + halt + wait for next cycle |
| INV-DRAWDOWN-50 | Pot drops >50% of starting → halt 24h + alert G | Guard check in heartbeat |
| INV-KILLSWITCH | killswitch.txt exists → HEARTBEAT_OK immediately | First check in HEARTBEAT.md |
| INV-DAILY-EXPOSURE-30 | Max 30% of pot deployed per day | Tracked in state.json |
| INV-NO-MARKETPLACE | Zero ClawHub/marketplace skills. All custom-built. | allowBundled: [] in config |

---

## HOW SKILLS WORK IN OPENCLAW

Each skill is a `SKILL.md` that teaches the agent WHEN and HOW to use
a capability. The agent reads the SKILL.md (loaded into its prompt),
then executes commands via the built-in bash/exec tool.

**Example — Rug Warden skill:**

```markdown
---
name: rug_warden
description: Pre-trade token validation. Run before ANY trade attempt.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["HELIUS_API_KEY", "BIRDEYE_API_KEY"]}}}
---

# Rug Warden — Pre-Trade Validation

## When to use
Run this skill BEFORE every trade attempt. This is non-optional.
If this skill returns FAIL, the trade MUST NOT execute.

## How to use
```bash
python3 -m lib.skills.warden_check --token <MINT_ADDRESS>
```

## Output format
Returns JSON:
```json
{"verdict": "PASS|FAIL|WARN", "checks": {...}, "reasons": [...]}
```

## Rules
- FAIL = trade does not execute. NO OVERRIDE.
- WARN = proceed with higher conviction threshold only.
- PASS = proceed normally.
```

The agent sees this in its prompt, and when it wants to evaluate a token,
it runs the bash command. Our Python code does the actual API calls.

---

## HEARTBEAT.md (The Core Trading Cycle)

This replaces the custom heartbeat.py from v0.1. OpenClaw's built-in
heartbeat mechanism fires every 10 min and the agent reads this file.

```markdown
# AutistBoar Heartbeat Checklist

Follow these steps IN ORDER on every heartbeat. Do not skip steps.

## 1. Killswitch Check
- Check if file `killswitch.txt` exists in workspace root
- If YES → reply HEARTBEAT_OK immediately. Do nothing else.

## 2. State Orientation
- Read `state/latest.md` for current positions and recent activity
- Read `state/state.json` for portfolio numbers

## 3. Drawdown Guard (INV-DRAWDOWN-50)
- If current pot value < 50% of `starting_balance` in state.json:
  → Send Telegram alert to G: "DRAWDOWN HALT: pot at {X}% of starting"
  → Set `halted: true` in state.json
  → Reply HEARTBEAT_OK. Do nothing else.
- If `halted: true` and halt has been < 24h → HEARTBEAT_OK

## 4. Daily Exposure Check (INV-DAILY-EXPOSURE-30)
- Calculate today's total deployed from state.json
- If >= 30% of pot → no new entries allowed this cycle

## 5. Smart Money Oracle
- Run: `python3 -m lib.skills.oracle_query`
- Review whale accumulation signals

## 6. Narrative Hunter
- Run: `python3 -m lib.skills.narrative_scan`
- Review social + onchain momentum + new pool detection

## 7. Position Watchdog
- For each open position in state.json:
  - Check current price vs entry
  - If stop-loss (-20%) or take-profit (+100%) hit → prepare exit
  - If liquidity dropped significantly → prepare exit

## 8. Execute Exits
- For any positions flagged for exit:
  - Run: `python3 -m lib.skills.execute_swap --direction sell --token <MINT> --amount <AMT>`
  - Write autopsy bead: `python3 -m lib.skills.bead_write --type exit --data '<JSON>'`

## 9. Evaluate New Opportunities
- Cross-reference oracle signals with narrative signals
- Signal convergence required:
  - 2+ independent signals = consider entry
  - 1 signal only = document, do not trade
  - Conflicting signals = stand down

## 10. Pre-Trade Validation (INV-RUG-WARDEN-VETO)
- For any candidate token:
  - Run: `python3 -m lib.skills.warden_check --token <MINT>`
  - If FAIL → do not trade. Log reason.
  - If WARN → require 3+ signal convergence

## 11. Execute Entries
- Check conviction level and trade size:
  - ≤$50 → auto-execute
  - $50-$100 → require 2+ signal convergence
  - >$100 → send Telegram alert to G, DO NOT execute (INV-HUMAN-GATE-100)
- Run: `python3 -m lib.skills.execute_swap --direction buy --token <MINT> --amount <SOL_AMT>`
- Write autopsy bead: `python3 -m lib.skills.bead_write --type entry --data '<JSON>'`

## 12. Edge Bank Query (Before New Trades)
- Before any entry, check similar historical patterns:
  - Run: `python3 -m lib.skills.bead_query --context '<SIGNAL_SUMMARY>'`
  - Review: "Last N similar patterns: X rugged, Y succeeded"
  - Factor into conviction assessment

## 13. Update State
- Update `state/state.json` with new positions, PnL, daily exposure
- Update `state/latest.md` with human-readable summary

## 14. Report
- If any trade was executed or notable event occurred:
  → Send Telegram summary to G
- If nothing happened:
  → Reply HEARTBEAT_OK
```

---

## PHASE 1: INSTALL + CONFIGURE + SCAFFOLD (Session 1)

**Goal:** OpenClaw running locally with workspace, personality, guards, and
heartbeat working in dry-run mode. No real API calls yet.

### 1.1 Project Setup
- [ ] Create directory structure (workspace layout above)
- [ ] `.gitignore` — .env, beads/*, *.db, memory/*, state/*, __pycache__, .venv
- [ ] `.env.example` — all env vars with placeholder values
- [ ] `requirements.txt` — Python deps with pinned versions
- [ ] `pyproject.toml` — project metadata, pytest config

### 1.2 OpenClaw Installation (local dev)
- [ ] Verify Node ≥22 installed
- [ ] `npm install -g openclaw@latest`
- [ ] `openclaw onboard` — initial setup
- [ ] Set `agents.defaults.workspace` → `~/autisticboar`
- [ ] Verify: `openclaw doctor` — zero critical issues

### 1.3 Workspace Personality Files
- [ ] `AGENTS.md` — full operating rules, all 7 invariants, decision framework
- [ ] `SOUL.md` — AutistBoar persona (smart scout, direct, witty, honest)
- [ ] `USER.md` — G's identity, how to address them
- [ ] `IDENTITY.md` — name: AutistBoar, emoji: 🐗
- [ ] `TOOLS.md` — notes on Python tools, skill usage patterns

### 1.4 Config Files
- [ ] `config/risk.yaml` — circuit breakers, position limits, drawdown 50%
- [ ] `config/firehose.yaml` — API endpoints, rate limits, RPC fallback chain
- [ ] `openclaw.json` — model routing, heartbeat config (skeleton)

### 1.5 Guards (Python)
- [ ] `lib/guards/killswitch.py` — check for halt file
- [ ] `lib/guards/drawdown.py` — pot < 50% starting → halt + alert
- [ ] `lib/guards/risk.py` — daily exposure check, position limits
- [ ] Tests: `tests/test_guards.py`

### 1.6 State Management
- [ ] `state/state.json` — initial schema (starting_balance, positions, daily_exposure)
- [ ] `state/latest.md` — initial template
- [ ] State read/write utilities in `lib/`

### 1.7 HEARTBEAT.md
- [ ] Write the full 14-step checklist (above)
- [ ] Verify heartbeat fires locally: `openclaw gateway` → observe heartbeat runs

### 1.8 Dry-Run Verification
- [ ] Start gateway: `openclaw gateway --verbose`
- [ ] Verify heartbeat fires and reads HEARTBEAT.md
- [ ] Verify agent picks up AGENTS.md personality
- [ ] Verify killswitch check works (touch killswitch.txt → HEARTBEAT_OK)

### Phase 1 Exit Gates
- **GATE 1** (Structure): All workspace files exist, OpenClaw loads them
- **GATE 6** (Killswitch): killswitch.txt → agent returns HEARTBEAT_OK immediately
- **GATE 7** (Personality): Agent responds in-character per SOUL.md

---

## PHASE 2: SKILLS + API LAYER (Session 2)

**Goal:** All 5 skills functional with mocked API responses. Agent can
run the full heartbeat cycle with test data.

### 2.1 Base HTTP Client
- [ ] `lib/clients/base.py` — httpx async client, rate limiting, retry, exponential backoff
- [ ] RPC fallback logic: Helius → QuickNode → Public, backoff on 429/timeout

### 2.2 API Clients
- [ ] `lib/clients/helius.py` — RPC + token metadata + honeypot simulation
- [ ] `lib/clients/birdeye.py` — price, liquidity, holders, volume
- [ ] `lib/clients/nansen.py` — smart money flows, wallet PnL
- [ ] `lib/clients/x_api.py` — X search API (KOL mentions, sentiment)
- [ ] `lib/clients/jupiter.py` — swap quote + route construction
- [ ] `lib/clients/jito.py` — MEV-protected bundle submission

### 2.3 Skill CLI Entry Points
- [ ] `lib/skills/oracle_query.py` — query whale signals, output JSON
- [ ] `lib/skills/warden_check.py` — validate token, output PASS/FAIL/WARN
- [ ] `lib/skills/narrative_scan.py` — scan social + onchain, output decomposed factors
- [ ] `lib/skills/execute_swap.py` — construct + sign + submit trade (with --dry-run)
- [ ] `lib/skills/bead_write.py` — write autopsy bead to beads/
- [ ] `lib/skills/bead_query.py` — query similar historical patterns

### 2.4 Skill SKILL.md Files
- [ ] `skills/smart_money_oracle/SKILL.md` — instructions for oracle_query
- [ ] `skills/rug_warden/SKILL.md` — instructions for warden_check
- [ ] `skills/narrative_hunter/SKILL.md` — instructions for narrative_scan
- [ ] `skills/blind_executioner/SKILL.md` — instructions for execute_swap
- [ ] `skills/edge_bank/SKILL.md` — instructions for bead_write + bead_query

### 2.5 Edge Bank Storage
- [ ] `lib/edge/bank.py` — SQLite bead storage + vector column
- [ ] `lib/edge/embeddings.py` — sentence-transformers (all-MiniLM-L6-v2) wrapper
- [ ] Bead schema: entry/exit/reason/signals/outcome/timestamp

### 2.6 Mock Infrastructure
- [ ] `tests/mocks/` — all API mocks (Helius, Birdeye, Nansen, Jupiter, X)
- [ ] `tests/fixtures/historical_replays.json` — 10 Pump.fun launches (mix rugs + runners)
- [ ] `tests/fixtures/chaos_vectors.json` — congestion, RPC timeout, Jito rejection

### 2.7 Integration Test
- [ ] Full heartbeat cycle with mocked APIs → agent runs all 14 steps
- [ ] Verify skill outputs are parsed correctly by the agent
- [ ] Verify state.json and latest.md get updated

### Phase 2 Exit Gates
- **GATE 2** (Skills): All 5 skills have SKILL.md + implementation + tests
- **GATE 4** (Rug Warden): Known honeypot tokens → all FAIL
- **GATE 5** (Heartbeat): Full cycle completes with mocked data

---

## PHASE 3: EXECUTION + SIGNER (Session 3)

**Goal:** Real transaction construction, signer isolation, Telegram alerts.

### 3.1 Blind KeyMan
- [ ] `lib/signer/signer.py` — subprocess that reads key from env, signs payload, exits
- [ ] `lib/signer/keychain.py` — env var reader (VPS) / macOS keychain reader (dev)
- [ ] Process isolation: agent process does NOT have key in its env
- [ ] Test: `tests/test_signer.py` — key never appears in any output/log

### 3.2 Blind Executioner Integration
- [ ] Wire execute_swap.py → Jupiter quote → construct tx → pass to signer subprocess
- [ ] Signer returns signed tx → submit via Jito bundle
- [ ] Dynamic fee adjustment: query recent slot fees, set adaptive priority tip
- [ ] --dry-run flag: skip actual submission, log what WOULD happen

### 3.3 Telegram Channel Setup
- [ ] Configure Telegram bot token in openclaw.json
- [ ] Set G's chat ID as delivery target
- [ ] Test heartbeat delivery: alert messages arrive on Telegram
- [ ] Test: trade alerts, drawdown alerts, daily PnL summary

### 3.4 Cron Jobs
- [ ] Daily PnL summary: `openclaw cron add --name "Daily PnL" --cron "0 22 * * *" --session isolated ...`
- [ ] Weekly edge review: `openclaw cron add --name "Edge Review" --cron "0 9 * * 1" ...`

### 3.5 Integration Tests
- [ ] Full heartbeat with devnet swap execution
- [ ] Historical replay: 10 Pump.fun launches through full pipeline (mocked)
- [ ] Chaos scenarios: RPC timeout, Jito rejection, congestion
- [ ] Key isolation audit: grep all outputs for key patterns → zero matches

### Phase 3 Exit Gates
- **GATE 3** (Blind KeyMan): Key never in context, logs, beads, or output
- **GATE 8** (Dry Run): Full system end-to-end with mocked data

---

## PHASE 4: DEPLOY (Session 4)

**Goal:** Running on VPS, first live heartbeat, 24h monitoring.

### 4.1 bootstrap.sh
- [ ] Create non-root user: `autistboar`
- [ ] Install Node ≥22 + Python 3.12+ + pip
- [ ] `npm install -g openclaw@latest`
- [ ] `openclaw onboard --install-daemon`
- [ ] ufw: allow ssh + 443 only
- [ ] fail2ban: enabled
- [ ] unattended-upgrades: enabled
- [ ] SSH: key-only auth
- [ ] Clone repo, set up venv, install Python deps

### 4.2 Configuration
- [ ] `~/.openclaw/.env` on VPS with real API keys (chmod 600)
- [ ] `~/.openclaw/openclaw.json` pointing workspace to ~/autisticboar
- [ ] Private key set in signer env var (manually by G)
- [ ] `openclaw doctor --fix` — verify clean
- [ ] `chmod 700 ~/.openclaw && chmod 600 ~/.openclaw/openclaw.json`

### 4.3 Security Hardening
- [ ] Verify gateway binds to localhost only
- [ ] Set up Tailscale for remote access (optional)
- [ ] `openclaw security audit --deep` — zero critical issues
- [ ] Git-track config: `cd ~/.openclaw && git init`

### 4.4 Graduation
- [ ] Dry-run on VPS with real APIs, heartbeat in dry-run mode
- [ ] Fund burner wallet ($100 initial)
- [ ] Remove --dry-run flag from execute_swap
- [ ] First live heartbeat → observe on Telegram
- [ ] Monitor 24h → watch for crashes, false trades, injection attempts
- [ ] If clean: scale to $200-500

---

## TECH STACK

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Agent Runtime** | OpenClaw (Node.js) | The framework. Handles agent loop, model routing, heartbeat, sessions, memory. |
| **Execution Layer** | Python 3.12+ | Our custom code. Skills call Python via bash. |
| **HTTP Client** | httpx | Async, timeout control, connection pooling |
| **Config/Models** | Pydantic v2 | Typed, validated config for Python layer |
| **Tests** | pytest | Brief spec |
| **Embeddings** | sentence-transformers (all-MiniLM-L6-v2) | Small, fast, runs on VPS |
| **Vector Storage** | SQLite + sqlite-vec | Zero infrastructure, single file |
| **Solana SDK** | solders + solana-py | Transaction construction + RPC |
| **LLM (coordinator)** | DeepSeek R1 via OpenRouter | ~$0.50/M tokens, handles heartbeat + skills |
| **Alerts** | Telegram bot (OpenClaw built-in) | G's phone, native channel support |
| **Process mgmt** | OpenClaw daemon (systemd) | `openclaw onboard --install-daemon` handles this |

### Python Dependencies (requirements.txt)

```
httpx>=0.27
pydantic>=2.6
solders>=0.21
solana>=0.34
sentence-transformers>=3.0
sqlite-vec>=0.1
pytest>=8.0
pytest-asyncio>=0.23
python-dotenv>=1.0
pyyaml>=6.0
```

---

## COST BREAKDOWN (Revised — Dual Model)

| Component | Provider | Monthly |
|-----------|----------|---------|
| RPC + APIs | Helius Developer | $49 |
| Price/Liquidity | Birdeye Pro | $99 |
| Smart Money | Nansen Pro | $69 |
| MEV Protection | Jito Tips | $50-100 |
| VPS (Singapore) | Hostinger 4-8GB | $20 |
| LLM — Heartbeat (DeepSeek R1) | OpenRouter | $10-15 |
| LLM — Chat (Sonnet) | OpenRouter | $10-20 |
| LLM — Cron (Auto) | OpenRouter | $2-5 |
| Telegram bot | Free | $0 |
| **TOTAL** | | **$309-377/mo** |

LLM cost breakdown:
- Heartbeat: ~144 cycles/day × $0.002 = ~$8.60/mo
- Chat: ~10-20 interactions/day × $0.03 avg = ~$9-18/mo
- Cron: 2 jobs/day × $0.001 = ~$0.06/mo
- **Total LLM: ~$20-40/mo** (down from $50 estimate, thanks to model routing)

---

## WHAT CHANGED FROM v0.1

| Item | v0.1 (Wrong) | v0.2 (Correct) |
|------|-------------|----------------|
| Agent loop | Build from scratch | OpenClaw Pi runtime |
| Model routing | Build ourselves | openclaw.json config |
| Heartbeat | Custom heartbeat.py + systemd timer | HEARTBEAT.md + OpenClaw heartbeat |
| Memory | Custom state management | OpenClaw memory/ + compaction |
| Alerts | ntfy.sh (custom) | Telegram channel (built-in) |
| Cron | Custom systemd timers | OpenClaw cron system |
| Personality | CLAUDE.md (custom loader) | AGENTS.md + SOUL.md (auto-loaded) |
| Skills | Custom skill loader + registry | SKILL.md format (AgentSkills spec) |
| Process mgmt | systemd unit (custom) | `openclaw onboard --install-daemon` |
| Config | openclaw.json (custom schema) | openclaw.json (OpenClaw schema) |

---

## BUILD ORDER (Execution Sequence)

```
SESSION 1 — INSTALL + CONFIGURE + SCAFFOLD
  1. Create workspace directory structure
  2. Install OpenClaw locally
  3. Write AGENTS.md, SOUL.md, USER.md, IDENTITY.md, TOOLS.md
  4. Write config/risk.yaml, config/firehose.yaml
  5. Write Python guards (killswitch, drawdown, risk)
  6. Write state/state.json schema + state/latest.md template
  7. Write HEARTBEAT.md (full 14-step checklist)
  8. Configure openclaw.json (model, heartbeat, workspace)
  9. Start gateway, verify heartbeat fires
  → Gate check: 1, 6, 7

SESSION 2 — SKILLS + API LAYER
  1. Base HTTP client (rate limit, retry, backoff)
  2. API clients (Helius, Birdeye, Nansen, X, Jupiter, Jito)
  3. Skill CLI entry points (6 Python scripts)
  4. Skill SKILL.md files (5 skills)
  5. Edge Bank (SQLite + embeddings)
  6. Mock infrastructure
  7. Wire skills into heartbeat cycle
  8. Integration test with mocks
  → Gate check: 2, 4, 5

SESSION 3 — EXECUTION + SIGNER
  1. Blind KeyMan signer (subprocess isolation)
  2. Execute_swap integration (Jupiter + Jito + dynamic fees)
  3. Telegram channel setup + alert delivery
  4. Cron jobs (daily PnL, weekly review)
  5. Integration tests + chaos vectors
  6. Key isolation audit
  → Gate check: 3, 8

SESSION 4 — DEPLOY
  1. bootstrap.sh finalization
  2. Push to GitHub
  3. VPS provisioning + OpenClaw install
  4. .env + key configuration
  5. Security hardening + audit
  6. Dry-run on VPS
  7. Fund wallet + first live heartbeat
  8. 24h monitoring
```

---

## SKILL EXTENSIBILITY ROADMAP

Core 5 skills ship in v0.1. Extensions come AFTER the core is breathing.
All skills built by us — zero marketplace installs.

```
v0.1 (Core — this build):
  1. Smart Money Oracle (whale accumulation detection)
  2. Rug Warden (pre-trade validation)
  3. Narrative Hunter (social + onchain momentum)
  4. Blind Executioner (Jupiter swap + Jito MEV protection)
  5. Edge Bank (trade autopsy + vector recall)

v0.2 (Extensions — after core is stable):
  - Market morning briefing on demand ("brief me")
  - Portfolio summary on command ("how are we doing?")
  - "What did you do while I was sleeping?" recap
  - Perplexity research → PDF/MD report
  - Agent self-extends by writing new SKILL.md files (supervised)

Rule: Core 5 first. Extensions after v0.1 is breathing.
```

---

## RESOLVED QUESTIONS

| # | Question | Answer |
|---|----------|--------|
| Q1 | Telegram bot | Create via @BotFather during Phase 1 (5 min setup) |
| Q2 | Model routing | Sonnet for chat, DeepSeek R1 for heartbeat (confirmed) |
| Q3 | VPS | Provision during Phase 4 (Hostinger 4-8GB Singapore) |
| Q4 | Bundled skills | `allowBundled: []` — confirmed, custom only (INV-NO-MARKETPLACE) |
| Q5 | Repo init | Build through Phase 1, push when skeleton passes gates |
| Q6 | Solana network | Devnet for signer tests, mainnet at deploy |
| Q7 | Scope | Dual-mode: autonomous scout + interactive assistant |

---

*"A scout with good senses, sharp memory, and the discipline to walk away. That's the edge."*
