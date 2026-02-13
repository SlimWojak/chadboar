# AutistBoar System Optimization

**Generated:** 2026-02-10 13:41 UTC  
**Model:** sonar  
**Prompt:** I am building an autonomous Solana memecoin trading scout called AutistBoar. The system runs on a VPS with 10-minute heartbeat cycles. It uses: (1) Smart Money Oracle to detect whale accumulation via Nansen API, (2) Narrative Hunter to scan social momentum via X API and Birdeye, (3) Rug Warden for pre-trade token validation via Helius and Birdeye, (4) Edge Bank for trade autopsy storage and pattern learning, (5) Blind Executioner for MEV-protected swaps via Jupiter and Jito bundles. The system has invariants including: private key isolation, mandatory Rug Warden veto, human approval for trades >00, 50% drawdown circuit breaker, and 30% daily exposure limit. What are the optimal configuration strategies, API usage patterns, signal weighting approaches, and risk management techniques to maximize edge hunting prowess while maintaining safety? Focus on practical tuning advice for conviction scoring, position sizing, exit timing, and signal convergence thresholds.

---

### Optimal Configuration Strategies
Configure the 10-minute heartbeat cycles for batched processing: subscribe to real-time streams via Geyser, Solana Labs pubsub, or Goldrush API for new Raydium pools, liquidity events, and Jupiter routing updates instead of polling, enabling async handling of whale accumulation (Smart Money Oracle), social momentum (Narrative Hunter), and token validation (Rug Warden)[1][2]. Use low-latency RPC endpoints like Chainstack Trader Nodes or Warp transactions for sub-second responses during slot-aware execution (~400ms slots), prioritizing Jito bundles for MEV protection in Blind Executioner[1][4]. Isolate private keys in hardware wallets or secure enclaves, enforcing Rug Warden veto via Helius/Birdeye checks for LP locks, liquidity thresholds (>10 SOL), and rug risks before any swap[2].

### API Usage Patterns
- **Nansen API (Smart Money Oracle)**: Query whale wallets with >$1M profit filters, tracking accumulation patterns; batch requests every cycle to detect multi-wallet clusters[3].
- **X API & Birdeye (Narrative Hunter)**: Stream social volume spikes and chart momentum; filter for win-rate > past month average, avoiding over-reliance on single posts[3].
- **Helius & Birdeye (Rug Warden)**: Pre-trade: validate LP burns, holder distribution, and slippage; post-trade: log to Edge Bank for pattern learning[1][2].
- **Jupiter & Jito (Blind Executioner)**: Build/simulate/send tx cycle per trade—accurate slippage (0.5-1%), DEX routing (Raydium/Orca priority), priority fees; retry on failure, batch via computeBudgetProgram[1].
Rate-limit APIs to 10-min cycles, fallback to pubsub for on-chain events, and log all with slot/error status to Edge Bank[1][2].

### Signal Weighting Approaches for Conviction Scoring
Weight signals multiplicatively for a **conviction score** (0-100) to trigger trades only on convergence:

| Signal Source       | Weight | Threshold for +Points | Practical Tuning |
|---------------------|--------|-----------------------|------------------|
| **Smart Money (Nansen)** | 40%   | 3+ whales accumulating (>10k USD each) | Score +20 per whale; cap at 40 if clustered buys[3] |
| **Social Momentum (X/Birdeye)** | 30%  | Volume spike >5x avg + win-rate >60% | +15 for narrative alignment (e.g., viral themes); decay after 30min[3] |
| **Rug Warden (Helius/Birdeye)** | 20%  | LP >10 SOL locked, no honeypot flags | Binary: +20 pass/-100 veto[2] |
| **Historical Edge Bank** | 10%  | Pattern match >70% past winners | +10 for similar autopsy profiles[2] |

**Convergence threshold**: ≥80 score for entry; require 3/4 signals aligned. Tune via backtesting on Devnet/dry-runs: start conservative (90 threshold), lower to 75 after 1-week paper trading with tiny sizes[2][6][7].

### Position Sizing and Risk Management Techniques
Enforce invariants strictly: human approval for trades >0.1 SOL (not 00, assuming typo), 50% drawdown breaker (halt all), 30% daily exposure (portfolio max). Size positions dynamically:

- **Formula**: Size = (Conviction Score / 100) × (Bankroll × 1%) × (1 / Volatility), capped at 5% per trade[2].
- Start small (0.01-0.05 SOL) for testing; scale to 0.1-1 SOL on ≥90 score[3][7][8].
- **Diversification**: Max 5 concurrent positions, spread across wallets[2][5].
- **Circuit breakers**: Auto-sell on 20% stop-loss or 3x take-profit; daily P&L review via Edge Bank[2].

### Exit Timing Strategies
Automate exits for emotion-free precision:
- **Take-Profit**: Tiered—50% at 2x, 30% at 5x, trail remainder at 20% from peak[2].
- **Stop-Loss**: Immediate at -20% or Rug Warden flag post-entry[2].
- **Time-Based**: Sell after 60min if no momentum, or on social decay[1].
- **Smart Money Exit**: Mirror whale sells via Nansen[3].
Retry failed sells; log autopsies to Edge Bank for ML refinement (e.g., avoid chased pumps)[1][2]. Test all via simulation before live[2][6].

---

**Citations:**

1. https://rpcfast.com/blog/solana-trading-bot-guide
2. https://goldrush.dev/guides/building-a-solana-memecoin-sniper-bot-using-the-goldrush-streaming-api-part-1/
3. https://www.youtube.com/watch?v=WFcGd0XmCYo
4. https://chainstack.com/how-to-build-a-solana-trading-bot/
5. https://getblock.io/blog/best-sol-trading-bot/
6. https://www.youtube.com/watch?v=AK8KTg5bisA
7. https://community.latenode.com/t/best-practices-for-getting-started-with-solana-memecoin-automated-trading-through-telegram/33812
8. https://learn.backpack.exchange/articles/best-telegram-trading-bots-on-solana