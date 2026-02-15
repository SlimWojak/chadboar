# ChadBoar Skills

## oracle_query
Smart Money Oracle — 5-phase pipeline: **Phase 0** Mobula Pulse pre-discovery (bonding/bonded tokens from Pump.fun → Raydium migrations), **Phase 1** Nansen Token Screener (1h/24h fallback), **Phase 2** Flow Intelligence + Who Bought/Sold validation, **Phase 3** Jupiter DCA detection, **Phase 4** Smart Money Holdings scan. Phases run in parallel where possible. Mobula whale networth + portfolio enrichment runs concurrently with TGM pipeline. Pulse candidates include holder categorization (bundlers, snipers, pro traders), organic volume ratios, and ghost metadata detection. ~25-40 Nansen credits/cycle + ~8-11 Mobula credits/cycle.

## warden_check
Rug Warden — pre-trade token validation.

## narrative_scan
Narrative Hunter — scan social + onchain momentum.

## execute_swap
Blind Executioner — execute swap (buy/sell).

## bead_write
Edge Bank — write trade autopsy bead.

## bead_query
Edge Bank — query similar historical patterns.

## self_repair
Self-Repair — gateway diagnostics via Grok, whitelist commands, human-gate restarts.

## chain_status
Flight Recorder — tamper-evident hash chain health. Summary, full verification (`--verify`), recent beads (`--recent N`). Auto-verifies on boot (step 1c). Anchors Merkle roots to Solana via SPL Memo every 50 beads.

See TOOLS.md for CLI usage. See SKILLS_OVERVIEW.md for detailed architecture.