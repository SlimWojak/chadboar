# ChadBoar Skills Overview

**Last Updated:** 2026-02-14
**Total Skills:** 8 (7 active + 1 whitelisted reference)

---

## Core Trading Skills (5)

### 1. Smart Money Oracle
**Purpose:** Detect whale accumulation and smart money flows on Solana tokens using Nansen Token God Mode (TGM) suite
**When to Use:** Heartbeat step 5, or on-demand for specific token analysis
**Data Source:** Nansen API (Token Screener, Flow Intelligence, Who Bought/Sold, Jupiter DCAs, Smart Money Holdings)
**Output:** Enriched token signals with flow_intel, buyer_depth, dca_count, and holdings_delta

**4-Phase Pipeline:**
1. **Discovery** — Token Screener (5 credits): Filter Solana tokens by smart money inflows, volume, liquidity
2. **Validation** — Flow Intelligence + Who Bought/Sold (1 credit each, parallel per token): Segment flow breakdown + buyer/seller depth with labels
3. **DCA Detection** — Jupiter DCAs (1-5 credits per token): Active smart money DCA orders on Jupiter (top 3 candidates)
4. **Holdings Scan** — Smart Money Holdings (5 credits): Portfolio-wide 24h balance changes across smart money wallets

**Fallback:** If Token Screener fails, falls back to legacy `get_smart_money_transactions()` (dex-trades) approach

**Credit Budget per Cycle:** ~23-35 credits (~3,360-5,040/day at 10min intervals)

**Enriched Output Fields (per token):**
- `flow_intel`: `{smart_trader_net_usd, whale_net_usd, exchange_net_usd, fresh_wallet_net_usd, top_pnl_net_usd}`
- `buyer_depth`: `{smart_money_buyers, total_buy_volume_usd, smart_money_sellers, total_sell_volume_usd}`
- `dca_count`: Active smart money DCA orders
- `discovery_source`: `"screener"` or `"dex-trades"` (fallback)
- `holdings_delta`: Portfolio-wide smart money balance shifts (top-level array)

**Red Flags (fed to scoring):**
- `fresh_wallet_net_usd > $50,000` → -10 pts (fresh wallet concentration)
- `exchange_net_usd > 0` (inflow to exchanges = distribution) → -10 pts

**Thresholds:**
- ≥3 whales accumulating → PRIMARY signal (permission gate eligible)
- `buyer_depth.smart_money_buyers` used for more accurate whale count

**Command:**
```bash
python3 -m lib.skills.oracle_query
python3 -m lib.skills.oracle_query --token <MINT>  # specific token
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
- **X API:** Narrative Hunter (social sentiment)
- **Jito Block Engine:** Blind Executioner (MEV protection)

### Output Format
All skills output structured JSON to stdout.  
Errors go to stderr.  
Heartbeat runner parses JSON to make decisions.

---

## Triangulation Logic (v0.2)

**Permission Gate (A1):**  
- Require ≥2 PRIMARY sources for AUTO_EXECUTE
- PRIMARY sources: Oracle (≥3 whales), Narrative (≥5x volume)

**Partial Data Penalty (A2):**  
- Missing Oracle: 0.7x multiplier
- Missing Narrative: 0.8x multiplier
- ≥2 sources failed: OBSERVE-ONLY mode

**Red Flags (B1):**
- Volume concentration (Gini ≥0.8): −15 pts
- Dumper wallets (1-2): −15 pts
- Dumper wallets (≥3): VETO
- Fresh wallet inflow >$50k (TGM): −10 pts
- Exchange inflow / distribution pattern (TGM): −10 pts

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

---

## Dry-Run Mode

**Current Status:** Active (cycles 5/10 complete)

**Behavior:**
- All skills run normally
- Conviction scoring active
- Blind Executioner skipped (no real trades)
- Recommendations logged but not executed
- State tracking active (dry_run_cycles_completed)

**Purpose:**
- Validate triangulation tuning v0.2 logic
- Test permission gate + red flags under real market conditions
- Build initial bead corpus for pattern learning

---

## Future Enhancements

**Planned:**
1. **Liquidity Drop Veto** (5th veto) — if liquidity drops >50% during scoring
2. **Honeypot Simulation** — Rug Warden check #6 (currently stubbed)
3. **Multi-Token Correlation** — detect sector rotation signals
4. **Sentiment Decay Model** — narrative age weighting refinement

**Under Consideration:**
- Dynamic position sizing based on edge bank match %
- Partial exit automation (tier 1/2 take-profit)
- Cross-chain bridge monitoring (Wormhole, Portal)
