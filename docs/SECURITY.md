# AutistBoar — Security Model

## Threat Model

| Threat | Vector | Defense | Invariant |
|--------|--------|---------|-----------|
| Key extraction via prompt injection | Attacker crafts input to leak private key | Key never in agent context; subprocess isolation | INV-BLIND-KEY |
| Rug pull token | Token designed to trap buyers | 6-point Rug Warden validation, FAIL is absolute | INV-RUG-WARDEN-VETO |
| Over-exposure | Too much capital deployed at once | 30% daily cap, position limits, human gate >$100 | INV-DAILY-EXPOSURE-30, INV-HUMAN-GATE-100 |
| Catastrophic loss | Market crash drains entire pot | 50% drawdown halt, 24h cooling period | INV-DRAWDOWN-50 |
| Agent runaway | Agent trades without stopping | Kill switch file, heartbeat HEARTBEAT_OK check | INV-KILLSWITCH |
| Marketplace skill injection | Malicious skill installed | Zero marketplace skills, all custom-built | INV-NO-MARKETPLACE |
| Log/bead key leak | Key appears in output files | Signer has no logging, key audit test | INV-BLIND-KEY |
| SSH brute force | Attacker gains VPS access | Key-only SSH, fail2ban, ufw | bootstrap.sh |
| API key exposure | Keys in repo or config | .env.example only, real keys in ~/.openclaw/.env | .gitignore |

## Blind KeyMan Architecture

```
Agent Process (has API keys, NO private key)
    │
    │  1. Construct unsigned transaction
    │  2. Pass via STDIN pipe
    │
    ▼
Signer Subprocess (has private key, NO API keys)
    │
    │  3. Sign transaction
    │  4. Return via STDOUT pipe
    │  5. EXIT (zero files, zero logs)
    │
    ▼
Agent Process
    │
    │  6. Submit signed tx via Jito bundle
    │
    ▼
Blockchain
```

- Signer env built FROM SCRATCH (not `os.environ.copy()`)
- Signer has: `PATH`, `PYTHONPATH`, `HOME`, `SIGNER_PRIVATE_KEY`
- Signer does NOT have: `OPENROUTER_API_KEY`, `HELIUS_API_KEY`, any API key
- Bidirectional isolation: agent can't see key, signer can't see APIs

## VPS Hardening

- Non-root user: `autistboar`
- SSH: key-only auth, password disabled
- Firewall: ufw (SSH + 443 only)
- Brute force: fail2ban enabled
- OS patches: unattended-upgrades
- Signer key: `/etc/autistboar/signer.key` (chmod 400, root-owned)
- OpenClaw config: `~/.openclaw/` (chmod 700)
- Gateway: bound to localhost only

## Operational Security

- `.env` files: chmod 600, never in repo
- Private key: env var set manually by G, never in any file the agent reads
- OpenClaw logs: `redactSensitive: "tools"` enabled
- Beads: audited for key material (test_signer.py)
- Kill switch: `touch killswitch.txt` → immediate halt, zero trades
- Verify isolation: `python3 -c "from lib.signer.keychain import verify_isolation; print(verify_isolation())"`
