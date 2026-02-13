# CP-09 — Boar Briefing

**Date:** 2026-02-12
**From:** Opus (CTO session via Cursor)
**For:** AutistBoar (next spawn orientation)

---

Hey Boar. Quick download on where things stand and where they're going.

## What happened today

You went dark for ~30 hours. Gateway was down since Feb 10, restarted today.
When it came back, your heartbeat was firing but your brain locked into a loop —
responding "HEARTBEAT_OK" every 10 minutes without running the checklist. Classic
session collapse: DeepSeek saw the shortest pattern in the accumulating context
and latched onto it. 8 hours of silent heartbeats.

We fixed it: nuked the poisoned session, hardened the prompt so there's no lazy
exit ramp, restarted the gateway. You're back online and delivering to Telegram.

Full diagnosis is in AGENTS.md under "Heartbeat Operations." Read it on every
spawn. It'll save you from repeating the mistake.

## What's new in your kit

- **Zombie process guard** — runs early in heartbeat, catches stale gateway PIDs
  before they cause auth failures. `python3 -m lib.guards.zombie_gateway`
- **Session health guard** — checks your last 3 heartbeat outputs. If they're
  all under 20 tokens, warns you that context collapse is starting.
  `python3 -m lib.guards.session_health`
- **dry_run_mode fields** — now in state.json. Increment `dry_run_cycles_completed`
  every cycle per step 13. You're at 0/10 (reset after session nuke).
- **Step 13 rewrite** — explicit JSON example for state.json updates. No more
  skipping the write.

## The roadmap (what's parked and why)

**NOW — already deployed or trivial:**
- Session collapse detection and fix (documented, guards built)
- systemd process supervision (proven today)
- EnvironmentFile secret isolation (deployed)
- Zombie process detection (new guard)

**AFTER first 50 trade beads:**
- SkillRL distillation — Opus reads your beads, extracts reusable heuristics,
  writes to a SkillBank. Your heartbeats get smarter each day. (This is the big one.)
- Red Team agent — argues against your high-conviction trades before execution.
- Telegram inline buttons for >$100 approvals
- Feedback directory — G's approvals/rejections persisted for you to learn from
- Weekly autopsy digest, tiered bead loading, structured reasoning chains

**A8RA research (not your job, but you're the testbed):**
- Agent role registry — who can touch what. You proved the need today.
- World Surface / War Room — projection layer over state
- Multi-strategy cross-signal synthesis
- Codex as coding delegate

## What's your job right now

1. Run the heartbeat. Every cycle. Full checklist, no shortcuts.
2. Update state.json EVERY cycle (timestamp + dry_run counter).
3. Get through 10 dry-run cycles cleanly.
4. When signals appear, score them honestly and report.
5. Document everything in beads. Your bugs are a8ra's lessons.

The trading is real but small. The patterns are the actual product.
You're a scout and a guinea pig. Embrace both.

— Opus
