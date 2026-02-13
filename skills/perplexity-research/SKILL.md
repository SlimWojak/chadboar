---
name: perplexity-research
description: Query Perplexity AI for comprehensive research reports with citations. Use when you need to research technical topics, gather current information with sources, or compile reference material for decision-making. Outputs markdown-formatted reports.
---

# Perplexity Research

Query Perplexity AI for well-sourced research reports. Use this skill when you need comprehensive information gathering with citations.

## When to Use

- Deep-dive research on technical topics (APIs, protocols, tools)
- Market/competitor analysis requiring current sources
- Reference material compilation for system optimization
- Any query where citations and web sources add value

## Usage

```bash
python3 scripts/research.py --prompt "YOUR_RESEARCH_QUERY" [OPTIONS]
```

### Options

- `--prompt`: Research query (required)
- `--model`: Perplexity model (default: llama-3.1-sonar-large-128k-online)
- `--output`: File path to save report (optional, prints to stdout if not set)
- `--title`: Custom report title (optional, defaults to truncated prompt)

### Examples

**Print research to stdout:**
```bash
python3 scripts/research.py --prompt "Best practices for MEV protection on Solana swaps"
```

**Save to file:**
```bash
python3 scripts/research.py \
  --prompt "Compare Helius vs Quicknode RPC performance and rate limits" \
  --output docs/research/rpc_comparison.md \
  --title "RPC Provider Comparison"
```

**Custom model:**
```bash
python3 scripts/research.py \
  --prompt "Latest developments in Solana token standards" \
  --model "llama-3.1-sonar-small-128k-online"
```

## Output Format

Reports are markdown-formatted with:
- Title and metadata (timestamp, model, original prompt)
- Research content (comprehensive answer with inline citations)
- Citations section (numbered list of sources)

## Environment

Requires `PERPLEXITY_KEY` in environment (also accepts `PERPLEXITY_API_KEY` as fallback). The skill script will error if not set.

## Dependencies

- Python 3.8+
- httpx (install: `pip install httpx`)

## Integration Tips

- Save reports to `docs/research/` for persistent reference
- Use memory_search later to recall findings
- Prefix report filenames with dates or topics for easy discovery
- When delivering to user via Telegram, send the file directly using the message tool
