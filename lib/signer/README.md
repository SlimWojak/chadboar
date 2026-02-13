# Blind KeyMan — Security Model

## Principle

> "The agent requests action. The signer executes. Neither sees the other's secrets."

## Architecture

```
AGENT PROCESS                           SIGNER SUBPROCESS
─────────────                           ──────────────────
os.environ:                             env (MINIMAL, built from scratch):
  OPENROUTER_API_KEY ✓                    PATH ✓
  HELIUS_API_KEY ✓                        PYTHONPATH ✓
  BIRDEYE_API_KEY ✓                       HOME ✓
  NANSEN_API_KEY ✓                        SIGNER_PRIVATE_KEY ✓ ← ONLY HERE
  X_BEARER_TOKEN ✓
  SIGNER_PRIVATE_KEY ✗ ← NEVER           OPENROUTER_API_KEY ✗ ← NOT HERE
                                          HELIUS_API_KEY ✗
                                          (no API keys at all)

  │                                       │
  │ 1. Construct unsigned tx              │
  │ 2. base64 encode                      │
  │ 3. Pass via STDIN pipe ──────────────►│ 4. Read key from own env
  │                                       │ 5. Read tx from stdin
  │                                       │ 6. Sign transaction
  │◄────────────────── STDOUT pipe ───────│ 7. Write signed tx to stdout
  │ 8. Read signed tx                     │ 8. EXIT (no files, no logs)
  │ 9. Submit via Jito                    │
  │                                       │
```

## Key Storage

| Environment | Storage | Access |
|-------------|---------|--------|
| VPS (production) | File at SIGNER_KEY_PATH (chmod 400, owned by signer user) | keychain.py reads, passes to subprocess env |
| macOS (dev) | macOS Keychain (`security find-generic-password -s autistboar-signer`) | keychain.py queries keychain |
| Testing | AUTISTBOAR_SIGNER_KEY env var | Test-only, never in production |

## Threat Model

| Threat | Defense |
|--------|---------|
| Prompt injection → agent compromised | Key not in agent context or env |
| Log leak | Signer writes zero logs |
| Bead leak | Key never in any bead |
| Skill compromise | Skills run in agent context → no key there |
| Agent env dump | SIGNER_PRIVATE_KEY not in agent's os.environ |
| Signer env leak | Signer env is minimal (no API keys to steal) |
| File system scan | Key file is chmod 400, owned by separate user |

## Invariant Verification

`keychain.verify_isolation()` can be called during heartbeat to continuously
verify that the agent process does not have key material in its environment.

## Setup

### macOS (Development)
```bash
# Store key in macOS Keychain
security add-generic-password -s autistboar-signer -a autistboar -w '<base64_private_key>'

# Verify it's stored
security find-generic-password -s autistboar-signer -w
```

### VPS (Production)
```bash
# As root: create key file
echo '<base64_private_key>' > /etc/autistboar/signer.key
chmod 400 /etc/autistboar/signer.key
chown root:root /etc/autistboar/signer.key

# Set path in agent's env
export SIGNER_KEY_PATH=/etc/autistboar/signer.key
```
