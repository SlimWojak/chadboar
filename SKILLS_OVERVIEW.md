# ChadBoar Skills Overview

**Last Updated:** 2026-02-14
**Total Skills:** 8 (7 active + 1 whitelisted reference)

---

## Core Trading Skills (5)

### 1. Smart Money Oracle
**Purpose:** Detect whale accumulation and smart money flows on Solana tokens using Nansen TGM suite, Mobula whale tracking, and Mobula Pulse pre-discovery
**When to Use:** Heartbeat step 5, or on-demand for specific token analysis
**Data Sources:** Nansen API, Mobula API (wallet + Pulse), Helius API
**Output:** Enriched token signals with flow_intel, buyer_depth, dca_count, holdings_delta, pulse candidates, and phase diagnostics

#### Execution Architecture

Three parallel pipelines run concurrently via `asyncio.gather()`:

```
query_oracle()
  ├─ _run_tgm_pipeline(nansen)       # Nansen Phases 1-4 (parallel Phase 4)
  ├─ _run_mobula_scan(mobula, whales) # Whale networth + portfolio enrichment
  └─ _run_pulse_scan(mobula)          # NEW: Pump.fun migration pre-discovery
```

#### Phase 0: Pulse Pre-Discovery (Mobula Pulse v2) — NEW

**The Edge:** Catches tokens during the Pump.fun → Raydium migration window — the 5-10 minute price discovery phase that Nansen/Birdeye cannot see yet. This is where cabal plays launch and early alpha lives.

**API Endpoint:**
```
GET https://pulse-v2-api.mobula.io/api/2/pulse
  ?chainId=solana:solana
  &assetMode=false
  &model=default
```

**Response Structure:** Returns three arrays by state:
- `bonding` — tokens still on Pump.fun bonding curve (pre-migration)
- `bonded` — tokens that just migrated to Raydium (prime entry window)
- `new` — recently created tokens (ignored — too early, too risky)

**Candidate Qualification Filters (applied in Python):**

| Filter | Threshold | Why |
|---|---|---|
| `liquidity` | > $5,000 | Tradeable — won't get stuck |
| `volume_1h` | > $10,000 | Real activity, not dead |
| `security.buyTax` | == 0 | No tax tokens |
| `security.sellTax` | == 0 | No tax tokens |
| `security.isBlacklisted` | == false | Not blacklisted |
| `security.honeypot` | == false | Not a honeypot |
| `top10HoldingsPercentage` | < 80% | Not fully insider-held |
| `bundlersHoldingsPercentage` | < 50% | Not a pure bundle play |

**Extracted Signal Fields (per Pulse candidate):**
```python
{
    "token_mint": address,
    "token_symbol": symbol,
    "bonding_state": "bonding" | "bonded",
    "liquidity_usd": liquidity,
    "volume_1h_usd": volume_1h,
    "organic_ratio": organic_volume_1h / volume_1h,  # Bot-excluded ratio
    "holders_count": holdersCount,
    "top10_pct": top10HoldingsPercentage,
    "bundler_pct": bundlersHoldingsPercentage,
    "sniper_pct": snipersHoldingsPercentage,
    "pro_trader_pct": proTradersHoldingsPercentage,
    "smart_trader_pct": smartTradersHoldingsPercentage,
    "ghost_metadata": bool,           # No socials + volume > $50k
    "deployer_migrations": int,       # Serial deployer count
    "minutes_since_created": float,
    "security": {                     # Pulse-native security data
        "mint_disabled": bool,
        "freeze_disabled": bool,
        "lp_locked": bool,
    },
    "source": "pulse",
    "discovery_source": "pulse-bonded" | "pulse-bonding",
}
```

**Ghost Metadata Detection:**
Cabals often launch tokens without social links (no Twitter, no Telegram, no website) to keep retail out during accumulation. Once links are added to DexScreener/Mobula, the "public" pump begins.
- Signal: `socials` is empty/null AND `volume_1h > $50,000`
- `ghost_metadata: true` → stealth launch indicator → **+5 conviction bonus**

**Pulse-Specific Scoring Signals (new fields in `SignalInput`):**

| Field | Type | Scoring Effect |
|---|---|---|
| `pulse_ghost_metadata` | bool | +5 pts bonus (stealth cabal accumulation window) |
| `pulse_organic_ratio` | float | < 0.3 → **−10 pts** red flag (fake/bot volume) |
| `pulse_bundler_pct` | float | > 20% → **−10 pts** red flag (bundled launch manipulation) |
| `pulse_sniper_pct` | float | > 30% → **−10 pts** red flag (sniper-dominated, likely dump) |
| `pulse_pro_trader_pct` | float | > 10% → +5 pts bonus (smart money entered at launch) |
| `pulse_deployer_migrations` | int | > 3 → **−10 pts** red flag (serial rug deployer) |

**Limit:** Top 5 candidates per cycle (sorted by `volume_1h` descending).

**Credits:** 1 Mobula credit per Pulse GET call (1/cycle).

#### Phase 1: Discovery — Nansen Token Screener

**Fallback Chain:** 1h screener → 24h screener → dex-trades
- If 1h returns 0 candidates, retry with `timeframe="24h"` (`discovery_source: "screener-24h"`)
- If 24h also empty, fall back to dex-trades (`discovery_source: "dex-trades"`)

**Credits:** 5 Nansen credits per screener call.

#### Phase 2: Validation — Flow Intelligence + Who Bought/Sold
Parallel enrichment per candidate. Segment flow breakdown + buyer/seller depth.
**Credits:** 1 Nansen credit each (2 per token, parallel).

#### Phase 3: DCA Detection — Jupiter DCAs
Active smart money DCA orders on Jupiter (top 3 candidates).
**Credits:** 1-5 Nansen credits per token.

#### Phase 4: Holdings Scan — Smart Money Holdings
Starts as `asyncio.create_task()` BEFORE Phase 1 (doesn't depend on candidates). Runs in parallel with Phases 1-3.
**Credits:** 5 Nansen credits.

#### Mobula Whale Scan (parallel with TGM)
- 5 tracked whale wallets queried via `asyncio.to_thread()` + `asyncio.gather()` (parallel, not sequential)
- Accumulating whales (accum_24h > $10k) enriched with `/wallet/portfolio` for token resolution
- Whale tokens enter scoring loop as `discovery_source: "mobula-whale"`

**Enriched Output Fields (per token):**
- `flow_intel`: `{smart_trader_net_usd, whale_net_usd, exchange_net_usd, fresh_wallet_net_usd, top_pnl_net_usd}`
- `buyer_depth`: `{smart_money_buyers, total_buy_volume_usd, smart_money_sellers, total_sell_volume_usd}`
- `dca_count`: Active smart money DCA orders
- `discovery_source`: `"screener"` | `"screener-24h"` | `"dex-trades"` | `"mobula-whale"` | `"pulse-bonded"` | `"pulse-bonding"`
- `holdings_delta`: Portfolio-wide smart money balance shifts (top-level array)
- `phase_timing`: Per-phase execution times (dict)
- `diagnostics`: Timestamped log lines from `_log()` (list)

**Red Flags (fed to scoring):**
- `fresh_wallet_net_usd > $50,000` → −10 pts (fresh wallet concentration)
- `exchange_net_usd > 0` (inflow to exchanges = distribution) → −10 pts
- `pulse_organic_ratio < 0.3` → −10 pts (fake volume)
- `pulse_bundler_pct > 20%` → −10 pts (bundled launch)
- `pulse_sniper_pct > 30%` → −10 pts (sniper-dominated)
- `pulse_deployer_migrations > 3` → −10 pts (serial rug deployer)

**Thresholds:**
- ≥3 whales accumulating → PRIMARY signal (permission gate eligible)
- `buyer_depth.smart_money_buyers` used for more accurate whale count

#### Triple-Lock Strategy (Pulse + Helius + Nansen convergence)

The highest-conviction play is when all three data sources converge on the same token:

| Source | Signal | Role |
|---|---|---|
| **Mobula Pulse** | Token just hit `bonded` state | **Speed** — first to see migration |
| **Helius/Rug Warden** | No honeypot, mint disabled, LP locked, clean launch | **Safety** — validates security |
| **Nansen** | Smart Money wallets entering within first 5 min | **Conviction** — whale confirmation |

When a Pulse candidate passes Rug Warden AND Nansen shows SM inflow, the scoring pipeline naturally produces high conviction (triple PRIMARY source convergence). No special override needed — the architecture handles it.

#### Credit Budget (Mobula — Startup Plan: 125,000/month)

| Endpoint | Credits/call | Calls/cycle | Daily (~250 cycles) | Monthly |
|---|---|---|---|---|
| Pulse GET | 1 | 1 | 250 | 7,500 |
| wallet/history | 1 | 5 | 1,250 | 37,500 |
| wallet/portfolio | 1 | ~2 avg | ~500 | ~15,000 |
| **Total Mobula** | | | **~2,000** | **~60,000** |

**Headroom:** 125,000 − 60,000 = **65,000 credits/month (52% buffer).**

Nansen credits are separate (their own API key/plan).

#### Implementation Plan

| # | File | Change |
|---|---|---|
| 1 | `config/firehose.yaml` | Add `pulse_url: "https://pulse-v2-api.mobula.io"` and `pulse: "/api/2/pulse"` endpoint |
| 2 | `lib/skills/oracle_query.py` | Add `get_pulse_listings()` to `MobulaClient` — GET Pulse, filter, extract candidates |
| 3 | `lib/skills/oracle_query.py` | Add `_run_pulse_scan()` async function — parallel with TGM + Mobula whale |
| 4 | `lib/skills/oracle_query.py` | Launch `_run_pulse_scan()` as third task in `query_oracle()` `asyncio.gather()` |
| 5 | `lib/scoring.py` | Add 6 new fields to `SignalInput`: `pulse_ghost_metadata`, `pulse_organic_ratio`, `pulse_bundler_pct`, `pulse_sniper_pct`, `pulse_pro_trader_pct`, `pulse_deployer_migrations` |
| 6 | `lib/scoring.py` | Add pulse scoring logic: bonuses (+5 ghost, +5 pro traders) and red flags (−10 each for organic, bundler, sniper, deployer) |
| 7 | `lib/heartbeat_runner.py` | Extract pulse candidates from oracle result, map pulse fields to `SignalInput`, feed into scoring loop |
| 8 | `tests/test_pulse_integration.py` | Test Pulse candidate filtering, ghost metadata detection, scoring bonuses/penalties, parallel execution |

**Command:**
```bash
python3 -m lib.skills.oracle_query
python3 -m lib.skills.oracle_query --token <MINT>  # specific token (skips Pulse)
```

---

### 2. Narrative Hunter
**Purpose:** Detect pre-pump narrative convergence from social + onchain signals  
**When to Use:** Heartbeat step 6, or on-demand for sentiment analysis  
**Data Sources:** X API (Twitter), Birdeye API  
**Output:** Tokens with volume spikes, KOL mentions, narrative age  

**Key Metrics:**
- Volume spike multiple (1h vs 24h avg)
- KOL detection (≥10k followers)
- Narrative age (time since first detection)

**Thresholds:**
- ≥5x volume spike → PRIMARY signal (permission gate eligible)
- KOL mention → +10 pts bonus

**Command:**
```bash
python3 -m lib.skills.narrative_scan
python3 -m lib.skills.narrative_scan --token <MINT>  # specific token
python3 -m lib.skills.narrative_scan --topic "AI tokens"  # topic scan
```

---

### 3. Rug Warden
**Purpose:** Pre-trade token validation (6-point security check)  
**When to Use:** MANDATORY before every trade (INV-RUG-WARDEN-VETO)  
**Data Sources:** Birdeye API, Helius API  
**Output:** PASS / WARN / FAIL verdict  

**Validation Checks:**
1. **Liquidity:** ≥$10,000 USD
2. **Holder Concentration:** Top 10 holders <80% total supply
3. **Mint Authority:** Immutable (revoked)
4. **Freeze Authority:** Immutable (revoked)
5. **Token Age:** ≥5 minutes old
6. **LP Lock Status:** Locked or burned

**Decision Logic:**
- **FAIL** → VETO (trade blocked, no override)
- **WARN** → Reduce conviction, log concern
- **PASS** → Proceed to conviction scoring

**Command:**
```bash
python3 -m lib.skills.warden_check --token <MINT>
```

---

### 4. Blind Executioner
**Purpose:** Execute Jupiter swaps with MEV protection via Jito bundles  
**When to Use:** Heartbeat steps 8 (exits) and 12 (entries), or on-demand trades  
**Data Source:** Helius RPC, Jito Block Engine  
**Output:** Transaction signature or error details  

**Security Model:**
- Private key NEVER enters agent context (INV-BLIND-KEY)
- Signer runs as separate subprocess
- Agent constructs unsigned transactions only

**Commands:**
```bash
# Buy (entry)
python3 -m lib.skills.execute_swap --direction buy --token <MINT> --amount <SOL>

# Sell (exit)
python3 -m lib.skills.execute_swap --direction sell --token <MINT> --amount <TOKEN_AMOUNT>

# Dry-run (simulate only)
python3 -m lib.skills.execute_swap --direction buy --token <MINT> --amount <SOL> --dry-run
```

**Output Format:**
```json
{
  "status": "SUCCESS|FAILED|DRY_RUN",
  "tx_signature": "...",
  "amount_in_sol": 0.5,
  "amount_out": 123456,
  "slippage_pct": 0.5,
  "jito_bundle_id": "...",
  "error": null
}
```

---

### 5. Edge Bank
**Purpose:** Trade autopsy bead storage and vector recall for pattern learning  
**When to Use:**  
- **Write:** After every entry/exit (heartbeat steps 8, 12)
- **Query:** Before new entries (heartbeat step 10)

**Data Storage:** `beads/` directory (timestamped YAML files)  

**Key Features:**
- Decomposes every trade into signal factors + outcome
- Vector embedding for semantic pattern matching
- Historical win rate lookup for similar setups

**Commands:**
```bash
# Write autopsy bead
python3 -m lib.skills.bead_write --type entry --data '<JSON>'
python3 -m lib.skills.bead_write --type exit --data '<JSON>'

# Query similar patterns
python3 -m lib.skills.bead_query --context '<SIGNAL_SUMMARY>'
```

**Bead Structure (Entry):**
```json
{
  "token_mint": "...",
  "token_symbol": "...",
  "direction": "buy",
  "amount_sol": 0.5,
  "thesis": "3 whales + 10x volume + KOL mention",
  "signals": ["oracle:3_wallets", "narrative:10x_volume", "kol:detected"],
  "conviction_score": 90,
  "ordering_score": 90,
  "permission_score": 90,
  "red_flags": [],
  "vetoes_triggered": [],
  "outcome": "pending"
}
```

**Bead Structure (Exit):**
```json
{
  "exit_reason": "stop_loss_-20",
  "pnl_pct": -18.5,
  "pnl_sol": -0.09,
  "hold_time_minutes": 45,
  "peak_pnl_pct": 5.2,
  "outcome": "loss",
  "lesson": "Rug Warden PASS but dumper wallets present in top trades"
}
```

---

### 6. Self-Repair
**Purpose:** Automated gateway diagnosis via Grok — identify root cause of gateway failures and suggest fix commands
**When to Use:** Gateway collapse (NO_REPLY loops), zombie PIDs, crashes, or on-demand diagnostics
**Data Sources:** journalctl, systemctl (read-only), Grok 4.1 FAST for analysis
**Output:** Structured diagnosis with root cause, severity, and whitelisted fix command

**Whitelist (hardcoded, not configurable):**
- **Read-only (auto-execute):** `journalctl`, `systemctl status`, `git status`, `git log`
- **Human-gated (suggest only):** `systemctl restart`, `rm <session_file>`
- **Blocked:** Everything else (no cat, pip, curl, sudo, git push, etc.)

**Human-Gate Behavior:**
- Restart commands and session file deletion are NEVER auto-executed
- Skill sends the command to G via Telegram with `(HUMAN-GATE: copy-paste to execute)`
- G decides whether to run it

**Bead Logging:** Writes YAML to `beads/self-repair/` (no vector embeddings)

**Commands:**
```bash
# Full diagnosis (logs + Grok + Telegram alert)
python3 -m lib.skills.self_repair

# Status-only (systemctl, no Grok)
python3 -m lib.skills.self_repair --status-only
```

---

## Reference Skills (2)

### 7. Brave Search (Whitelisted)
**Purpose:** Search reference documentation only (NOT general web)  
**When to Use:** When agent needs to look up API docs or technical references  
**Whitelist Domains:**
- openrouter.ai
- docs.helius.dev
- docs.birdeye.so
- docs.nansen.ai
- github.com
- docs.jup.ag
- docs.jito.network
- solana.com
- stackoverflow.com

**Enforcement:** Domain whitelist enforced in skill code (INV-BRAVE-WHITELIST)

**Command:**
```bash
python3 -m lib.skills.brave_search --query "Birdeye API token security endpoint"
```

---

### 8. Perplexity Research
**Purpose:** Comprehensive research reports with citations (technical deep-dives)  
**When to Use:** When G requests research on a topic, or agent needs to compile reference material  
**Output:** Markdown-formatted reports with source citations  

**Command:**
```bash
python3 -m lib.skills.perplexity_query --query "Solana MEV protection strategies"
```

---

## Skill Usage Guardrails

### Mandatory Execution Order (Heartbeat)
1. **Killswitch Check** (lib/guards/killswitch.py)
2. **Drawdown Guard** (lib/guards/drawdown.py)
3. **Risk Limits** (lib/guards/risk.py)
4. **Smart Money Oracle** (step 5)
5. **Narrative Hunter** (step 6)
6. **Position Watchdog** (step 7)
7. **Execute Exits** (step 8, uses Blind Executioner + Edge Bank)
8. **Edge Bank Query** (step 10)
9. **Rug Warden** (step 11, before scoring)
10. **Conviction Scoring** (lib/scoring.py, internal)
11. **Execute Entries** (step 12, uses Blind Executioner + Edge Bank)

### Non-Negotiable Rules
- **INV-RUG-WARDEN-VETO:** If Rug Warden returns FAIL, trade does not execute (no override)
- **INV-BLIND-KEY:** Private key never enters agent context (Blind Executioner enforces)
- **INV-NO-MARKETPLACE:** Only custom workspace skills used (never ClawHub/marketplace)
- **INV-BRAVE-WHITELIST:** Brave search restricted to approved reference docs only

---

## Skill Architecture

### Python Execution Layer
All skills are Python modules in `lib/skills/` and `lib/guards/`.  
Executed via bash from workspace root:
```bash
cd /home/autistboar/chadboar && .venv/bin/python3 -m <module>
```

### API Dependencies
- **Helius API:** Rug Warden (token metadata), Blind Executioner (RPC)
- **Birdeye API:** Narrative Hunter (volume data), Rug Warden (security checks)
- **Nansen API:** Smart Money Oracle (Token Screener, Flow Intelligence, Who Bought/Sold, Jupiter DCAs, Smart Money Holdings, dex-trades fallback)
- **Mobula API:** Smart Money Oracle — whale networth tracking (`/wallet/history`), whale token resolution (`/wallet/portfolio`), Pulse pre-discovery (`/api/2/pulse` via `pulse-v2-api.mobula.io`). Startup plan: 125k credits/month.
- **X API:** Narrative Hunter (social sentiment)
- **Jito Block Engine:** Blind Executioner (MEV protection)

### Output Format
All skills output structured JSON to stdout.  
Errors go to stderr.  
Heartbeat runner parses JSON to make decisions.

---

## Triangulation Logic (v0.4 — Pulse-aware)

**Permission Gate (A1):**
- Require ≥2 PRIMARY sources for AUTO_EXECUTE
- PRIMARY sources: Oracle (≥3 whales), Narrative (≥5x volume), Pulse (bonded + pro_trader_pct > 10%)

**Partial Data Penalty (A2):**
- Missing Oracle: 0.7x multiplier
- Missing Narrative: 0.8x multiplier
- Missing Pulse: no penalty (additive source, not required)
- ≥2 sources failed: OBSERVE-ONLY mode

**Red Flags (B1):**
- Volume concentration (Gini ≥0.8): −15 pts
- Dumper wallets (1-2): −15 pts
- Dumper wallets (≥3): VETO
- Fresh wallet inflow >$50k (TGM): −10 pts
- Exchange inflow / distribution pattern (TGM): −10 pts
- Fake volume / low organic ratio < 0.3 (Pulse): −10 pts
- Bundler concentration > 20% (Pulse): −10 pts
- Sniper concentration > 30% (Pulse): −10 pts
- Serial deployer > 3 migrations (Pulse): −10 pts

**Bonuses (Pulse-specific):**
- Ghost metadata (no socials + volume > $50k): +5 pts
- Pro trader holdings > 10% at launch: +5 pts

**Vetoes (5 total):**
1. Rug Warden FAIL
2. ≥3 dumper wallets
3. Token <2min old
4. Wash trading (≥10x volume + no KOL)
5. Liquidity drop (TODO)

**Time Mismatch (B2):**  
- Oracle + Narrative timestamps <5min apart → downgrade 1 tier

**Ordering vs Permission Split (C1):**  
- Ordering score: "greedy" conviction (original logic)
- Permission score: penalized with red flags + vetoes
- Both logged in beads for learning
- Permission score governs final action

### VSM Coordination (v0.3)

**S2 Divergence Damping:**
- If oracle detects ≥2 whales but narrative shows <2x volume and no KOL → −25 pts penalty
- Prevents trading on whale accumulation without organic social discovery
- Stacks with existing partial data penalties (orthogonal triggers)

**S5 Arbitration Alert:**
- When Grok alpha override says TRADE but divergence damping or low permission (<50) conflicts
- Auto-downgrade to WATCHLIST + Telegram ⚖️ alert to G
- G can manually override — guards win by default

---

## Dry-Run Mode

**Current Status:** Disabled (live trading active)

**Behavior (when enabled):**
- All skills run normally
- Conviction scoring active
- Blind Executioner skipped (no real trades)
- Recommendations logged but not executed
- State tracking active (dry_run_cycles_completed)

---

## Future Enhancements

**In Progress:**
1. **Mobula Pulse Integration** — Phase 0 pre-discovery for Pump.fun → Raydium migrations. Design complete (see Oracle section above). Implementation next.

**Planned:**
1. **Liquidity Drop Veto** (5th veto) — if liquidity drops >50% during scoring
2. **Honeypot Simulation** — Rug Warden check #6 (currently stubbed)
3. **Multi-Token Correlation** — detect sector rotation signals
4. **Sentiment Decay Model** — narrative age weighting refinement

**Under Consideration:**
- Dynamic position sizing based on edge bank match %
- Partial exit automation (tier 1/2 take-profit)
- Cabal rotation detection (Mobula wallet graph: where did top holders get their SOL? If from a previous 100x winner → same cabal). Requires Growth plan for credit budget.
- Mobula Pulse WebSocket feed (Growth plan, $400/month) — real-time streaming instead of polling. Would reduce latency from 5min to <1s.
- Cross-chain bridge monitoring (Wormhole, Portal)
