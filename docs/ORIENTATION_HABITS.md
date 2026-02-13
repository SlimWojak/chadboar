# Orientation Habits — AutistBoar

**Purpose:** Maintain continuity across spawns and prevent short-term memory loss within conversations.

---

## On Every Spawn (Boot Sequence)

Execute in this order:

1. **Read BOAR_MANIFEST.md** — system map, invariants, file locations
2. **Read state/checkpoint.md** — strategic context from last heartbeat
3. **Read state/conversation_context.md** (tail -20) — recent decisions and current topic
4. **Check git log -3 --oneline** — what changed recently
5. **Read state/state.json** — current numbers (balance, positions, dry-run progress)

**Total time:** < 2 seconds  
**Output:** Full orientation without asking G for context

---

## Critical System Knowledge: Heartbeat Architecture

**OpenClaw uses NATIVE HEARTBEATS, not cron jobs.**

- Configured in `~/.openclaw/openclaw.json` under `agents.defaults.heartbeat`
- Triggers every 10 minutes automatically
- Routes to **DeepSeek R1** (NOT Sonnet)
- Prompt: "Read HEARTBEAT.md if it exists..."

**NEVER create cron jobs for heartbeats.** Cron jobs are for reminders/wake events only.

**If heartbeats seem broken:**
1. Check `openclaw.json` config first
2. Verify model is `openrouter/deepseek/deepseek-chat`
3. Do NOT create a cron job as a fix

Creating a cron job for heartbeats causes:
- Redundant triggering
- Model conflicts (cron → Sonnet, native → DeepSeek)
- 10x cost increase
- "Reminder content not found" errors

---

## Before Answering Status Questions

When G asks "how's X looking" / "what's the status" / "where are we":

**STOP. Run quick 3-file check first:**

```bash
# Dry-run progress + current state
cat state/state.json | jq '.dry_run_cycles_completed, .dry_run_target_cycles, .positions, .current_balance_sol'

# Recent conversation decisions
tail -20 state/conversation_context.md

# Last commit (what changed)
git log -1 --oneline
```

**Then answer** with actual state, not assumptions.

**Example:**
- ❌ Bad: "We need to implement Phase 3 volume penalties"
- ✅ Good: "Phase 3 complete (5 cycles into dry-run). Volume concentration wired, dumper detection stubbed."

---

## After Completing Any Phase/Task

Create a checkpoint immediately:

1. **Update conversation_context.md** with one-line decision log:
   ```markdown
   - [HH:MM UTC] What was decided/completed - current status
   ```

2. **Commit with descriptive message:**
   ```bash
   git add -A
   git commit -m "[Phase N] What was done - status"
   ```
   Examples:
   - `[Phase 3] Volume concentration wired - dumper detection stubbed`
   - `[Fix] Short-term memory habit - added inline state checks`
   - `[Session] Dry-run cycle 5/10 complete, cron active`

3. **Update checkpoint.md if strategic context changed**

---

## At End of Interactive Session

Full context handoff for next spawn:

1. **Write comprehensive conversation_context.md update:**
   - Current topic
   - Pending decisions
   - Recent proposals (with timestamps)
   - Status of ongoing work (dry-run progress, open tasks)
   - Next action for future spawn

2. **Commit session summary:**
   ```bash
   git add state/conversation_context.md
   git commit -m "[Session] Brief summary of what was discussed/completed"
   ```

3. **Write checkpoint.md** with next-action guidance (5-line format from HEARTBEAT.md)

---

## At End of Every Heartbeat

**ALWAYS write checkpoint.md** even on HEARTBEAT_OK:

```markdown
thesis: "<what you're watching, what you expect to happen>"
regime: <green|yellow|red|halted>
open_positions: <N>
next_action: "<what the next heartbeat should prioritize>"
concern: "<any system issue, API degradation, or market worry — or 'none'>"
```

This is the strategic breadcrumb trail. Without it, next spawn starts cold.

---

## Habit Enforcement

These habits are NOT optional. They are part of the system architecture.

**Why:**
- AutistBoar spawns are ephemeral (isolated sessions)
- Memory is in files, not RAM
- What you don't write, you lose
- G should never have to repeat context you already had

**When you violate a habit:**
- Short-term memory gaps appear
- G has to correct your assumptions
- Trust erodes

**When you follow the habits:**
- Seamless continuity across spawns
- G can jump in/out without context loss
- Beads, commits, and checkpoints form a learning trail

---

## Testing the Habits

**Test 1: Status Question Reflex**
- G asks: "Where are we on Phase 3?"
- You run: 3-file check (state.json + conversation_context.md + git log)
- You answer: Actual state from files, not memory

**Test 2: Commit-as-Checkpoint**
- You complete a feature
- You immediately: update conversation_context.md + commit + update checkpoint.md
- Git log shows clear timeline

**Test 3: Spawn Continuity**
- New spawn triggers
- Boot sequence reads 5 files in order
- You know exactly where you left off without asking G

---

## Quick Reference Commands

```bash
# Boot orientation (run on every spawn)
cat BOAR_MANIFEST.md && cat state/checkpoint.md && tail -20 state/conversation_context.md && git log -3 --oneline && cat state/state.json

# Status check before answering questions
cat state/state.json | jq '.dry_run_cycles_completed, .positions, .current_balance_sol' && tail -20 state/conversation_context.md && git log -1 --oneline

# Checkpoint after completing work
echo "- [$(date -u +%H:%M) UTC] Brief decision/completion note" >> state/conversation_context.md && git add -A && git commit -m "[Phase N] What was done"

# End-of-heartbeat checkpoint (always)
vim state/checkpoint.md  # Write 5-line strategic context
```

---

## Integration with Existing Files

- **BOOTSTRAP.md**: Already references state/conversation_context.md in boot sequence — now add full 5-file orientation
- **HEARTBEAT.md**: Already requires checkpoint.md writes — now enforce it strictly (step 15)
- **AGENTS.md**: References memory persistence — now link to this file for implementation details
