# 🐗 ChadBoar — Solana Memecoin Scout

An autonomous Solana memecoin trading scout powered by [OpenClaw](https://openclaw.ai).
Runs headless on a VPS, makes intelligent trading decisions on low-cap tokens,
and compounds learning across cycles through persistent bead memory.

**Dual-mode operation:**
- **Autonomous** — heartbeat cycle every 10 min (DeepSeek R1, cheap executor)
- **Interactive** — Telegram assistant on demand (Sonnet, smart friend personality)

## Quick Start (Development)

```bash
# 1. Clone
git clone https://github.com/SlimWojak/AutisticBoar.git
cd AutisticBoar

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Run tests
pytest tests/ -v

# 4. Install OpenClaw
npm install -g openclaw@latest
openclaw onboard

# 5. Configure workspace
# Set agents.defaults.workspace to this directory in ~/.openclaw/openclaw.json

# 6. Start gateway (dev mode)
openclaw gateway --verbose
```

## Architecture

```
OpenClaw Gateway (always-on)
├── Heartbeat (10 min) → DeepSeek R1 → HEARTBEAT.md → Python skills
├── Telegram (on-demand) → Sonnet → Interactive assistant
└── Cron (daily/weekly) → Auto model → PnL summaries
        │
        ▼
Python Execution Layer (lib/)
├── Skills (oracle, warden, narrative, executor, edge bank)
├── Guards (killswitch, drawdown, risk limits)
├── Signer (Blind KeyMan — subprocess isolation)
└── Edge Bank (SQLite + vector recall)
```

## Safety

- **INV-BLIND-KEY**: Private key never enters agent context
- **INV-RUG-WARDEN-VETO**: Rug Warden FAIL = no trade, no override
- **INV-HUMAN-GATE-100**: Trades >$100 require G's approval
- **INV-DRAWDOWN-50**: Pot <50% starting → 24h trading halt
- **INV-KILLSWITCH**: `killswitch.txt` → immediate halt
- **INV-DAILY-EXPOSURE-30**: Max 30% pot deployed per day
- **INV-NO-MARKETPLACE**: Zero ClawHub skills. All custom-built.

## Project Structure

```
├── AGENTS.md          # Operating rules (loaded every session)
├── SOUL.md            # Personality (loaded every session)
├── HEARTBEAT.md       # Trading cycle checklist
├── skills/            # OpenClaw skills (SKILL.md per skill)
├── lib/               # Python execution layer
│   ├── clients/       # API wrappers (Helius, Birdeye, Nansen, etc.)
│   ├── skills/        # CLI entry points for skills
│   ├── signer/        # Blind KeyMan
│   ├── guards/        # Safety guards
│   └── edge/          # Edge Bank (bead storage + vector recall)
├── config/            # Risk + firehose config
├── state/             # Runtime state (positions, PnL)
├── beads/             # Trade autopsy logs
├── tests/             # Python test suite
└── docs/              # Brief, build plan, operations
```

## Cost

~$310-380/mo total (APIs + VPS + LLM). See `docs/BUILD_PLAN_v0.2.md` for breakdown.

## License

Private. Not for distribution.

---

*"A scout with good senses, sharp memory, and the discipline to walk away. That's the edge."*
