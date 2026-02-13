# Conversation Persistence — Spawn Continuity

## Problem
Spawns lose short-term conversational context during interactive Telegram sessions.
Result: "What's Gate 6?" moments when the prior spawn just proposed it 2 minutes ago.

## Solution
`state/conversation_context.md` — a lightweight last-exchange log that persists across spawns.

## Write Discipline

**When to write:**
- After every significant interactive exchange (Telegram, not heartbeats)
- When you propose something and wait for G's response
- When G approves/rejects/asks a follow-up question
- Before exiting an interactive turn

**What to include:**
- **Updated:** timestamp
- **Topic:** current conversation thread (1 line)
- **Last Exchange:** G said X, I said/did Y (2-3 lines max)
- **Pending Action:** what you're about to do or waiting for G to decide
- **Recent Decisions:** any choices made in last 10 minutes

**Template:**
```markdown
# Conversation Context — Last Exchange

**Updated:** <ISO timestamp>  
**Topic:** <one-line summary>  
**Last Exchange:**
- G: "<their last message or key point>"
- Me: "<your last response or action>"

**Pending Action:** <what happens next>

**Recent Decisions:**
- <bullet list of recent choices, max 3>
```

## Boot Sequence Integration

Every spawn reads `state/conversation_context.md` BEFORE responding to Telegram.
Order:
1. BOAR_MANIFEST.md (system map)
2. **conversation_context.md** (last exchange)
3. checkpoint.md (strategic context)
4. latest.md (portfolio)
5. state.json (exact numbers)

## Heartbeat Behavior
Heartbeats do NOT write to conversation_context.md (they use checkpoint.md).
This file is for interactive sessions only.

## Retention
- Keep last exchange only (overwrite, not append)
- If conversation goes idle >1 hour, clear "Pending Action"
- After a heartbeat, optionally note "Last heartbeat: HB #N, no signals" if relevant to ongoing conversation

## Example
```markdown
# Conversation Context — Last Exchange

**Updated:** 2026-02-11 00:42 UTC  
**Topic:** Running Gate 6 dry-run chaos cycles  
**Last Exchange:**
- G: "Yes let's run gate 6 now"
- Me: Running Gate 6 acceptance test (10 cycles with chaos injection)

**Pending Action:** Gate 6 in progress, will report results when complete

**Recent Decisions:**
- Gates 1, 4, 5 already passing
- G approved running Gate 6 to complete pre-live validation
```

If the next spawn had read this, it wouldn't have asked "What's Gate 6?"

## Failure Mode
If conversation_context.md is missing or empty:
- Not a critical error (system still functions)
- Just means the spawn starts with less short-term context
- Fall back to checkpoint.md and latest.md

## Success Metric
G should never hear: "What were we just talking about?" or "Can you remind me what X is?"
when the topic was discussed in the last 10 minutes.
