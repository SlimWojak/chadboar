---
name: brave-search
description: Reference documentation search (restricted to approved technical docs only)
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["BRAVE_API_KEY"]}}}
---

# Brave Search — Reference Documentation Only

## When to use
Use when you need to look up technical documentation for:
- OpenRouter API docs
- Solana RPC/blockchain APIs (Helius, Birdeye, Nansen, Jupiter, Jito)
- GitHub repository documentation
- StackOverflow technical answers

**NOT allowed:** Twitter, Reddit, forums, news sites, general web.

## How to use
```bash
cd /home/autistboar/autisticboar && .venv/bin/python3 -m lib.skills.brave_search --query "<search query>"
```

## Output format
Returns JSON:
```json
{
  "status": "OK|ERROR",
  "results": [
    {
      "title": "...",
      "url": "...",
      "description": "...",
      "domain": "..."
    }
  ],
  "count": 5,
  "blocked_domains": []
}
```

## Domain Whitelist (INV-BRAVE-WHITELIST)

**ENFORCED IN CODE. NON-NEGOTIABLE.**

Allowed domains:
- `openrouter.ai`
- `docs.helius.dev`
- `docs.birdeye.so`
- `docs.nansen.ai`
- `github.com`
- `docs.jup.ag`
- `docs.jito.network`
- `solana.com`
- `stackoverflow.com`

Any result from a domain NOT on this list is filtered out before returning.

## Error cases
- No `BRAVE_API_KEY` → returns ERROR with message
- Query blocked (contains injection patterns) → returns ERROR
- All results filtered (none from whitelisted domains) → returns empty list
