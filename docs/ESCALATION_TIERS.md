# Escalation Tiers — Telegram Alert System

All Telegram messages to G must be prefixed with the appropriate tier emoji.
This is not optional. G triages by emoji on mobile.

## 🔴 CRITICAL — Immediate Attention Required

Triggers:
- Drawdown halt triggered (INV-DRAWDOWN-50)
- Signer error / key isolation violation
- Prompt injection attempt detected
- Kill switch activated
- 5+ consecutive heartbeat failures

Format:
```
🔴 CRITICAL: Drawdown halt — pot at 42% of starting ($420/$1000)
Trading halted for 24h. Activated at 14:32 UTC.
```

## 🟡 WARNING — Attention, No Emergency

Triggers:
- 3+ rug detections in 24h (market is hostile)
- API provider failing (429s, timeouts, 3+ consecutive)
- Position approaching stop-loss (down >10%, threshold is 20%)
- Daily loss exceeding 5% of pot
- Consecutive losses circuit breaker triggered (3+)
- Heartbeat took >60s (usually <10s)

Format:
```
🟡 WARNING: Birdeye API failing — 4 timeouts in last hour
Falling back to cached data. Monitoring.
```

## 🟢 INFO — Normal Operations, Notable Event

Triggers:
- Trade executed (entry or exit)
- Position closed (win or loss, with PnL)
- Strong signal convergence detected (even if not traded)
- Watchdog exit trigger fired (stop-loss/take-profit)
- New high-conviction opportunity identified

Format:
```
🟢 ENTRY: Bought $BOAR — 0.5 SOL @ $0.00123
Thesis: 4 whale wallets + 5x volume spike + KOL mentions
Rug Warden: PASS (liquidity $85k, 35% top holders)
```

```
🟢 EXIT: Sold $BOAR — 0.75 SOL (+50%)
Held: 2h 15m. Take-profit at 2x not reached, but liquidity dropping.
```

## 📊 DIGEST — Scheduled Summaries

Triggers:
- Daily PnL cron (10 PM SGT)
- Weekly edge review (Monday 9 AM SGT)
- On-demand briefing (G asks "how are we doing?")

Format:
```
📊 DAILY PnL — Feb 10, 2026
Pot: 5.2 SOL ($936) | Day: +0.3 SOL (+6.1%)
Trades: 2 entries, 1 exit (1W 0L)
Open: 3 positions | Exposure: 22% of pot
Top signal: $MEME whale accumulation continuing
```

## Rules

1. Every Telegram message gets exactly one prefix emoji. No exceptions.
2. Don't combine tiers. If it's both WARNING and INFO, use the higher tier.
3. Keep messages mobile-friendly — 3-5 lines max for INFO/WARNING.
4. CRITICAL messages can be longer if context is needed.
5. DIGEST can be structured with bullet points.
