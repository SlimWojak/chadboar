# Tools — AutistBoar

## Python Execution Layer

All custom skills call Python scripts via bash. The Python layer lives in `lib/`.
Run commands from the workspace root (`~/autisticboar/`).

### Skill Commands

```bash
# Smart Money Oracle — query whale accumulation signals
python3 -m lib.skills.oracle_query

# Rug Warden — pre-trade token validation
python3 -m lib.skills.warden_check --token <MINT_ADDRESS>

# Narrative Hunter — scan social + onchain momentum
python3 -m lib.skills.narrative_scan

# Blind Executioner — execute swap (buy/sell)
python3 -m lib.skills.execute_swap --direction <buy|sell> --token <MINT> --amount <SOL>

# Edge Bank — write trade autopsy bead
python3 -m lib.skills.bead_write --type <entry|exit> --data '<JSON>'

# Edge Bank — query similar historical patterns
python3 -m lib.skills.bead_query --context '<SIGNAL_SUMMARY>'
```

### Guard Commands

```bash
# Check killswitch
python3 -m lib.guards.killswitch

# Check drawdown guard
python3 -m lib.guards.drawdown

# Check daily risk limits
python3 -m lib.guards.risk
```

## Output Format

All skill commands output structured JSON to stdout. Parse the JSON to make
decisions. Errors go to stderr.

## Environment

Python scripts read API keys from environment variables. These are injected
by OpenClaw's skill config system — you do not need to manage them manually.

## Dry-Run Mode

All execution commands support `--dry-run` flag. When set, they simulate
the action and log what WOULD happen without actually executing.
