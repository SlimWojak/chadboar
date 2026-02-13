"""Brave Search — Reference documentation search only.

Restricted to whitelisted technical domains. No social media, forums, or general web.

Usage:
    python3 -m lib.skills.brave_search --query "OpenRouter API auth headers"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# INV-BRAVE-WHITELIST — Allowed domains for search results
ALLOWED_DOMAINS = {
    "openrouter.ai",
    "docs.helius.dev",
    "docs.birdeye.so",
    "docs.nansen.ai",
    "github.com",
    "docs.jup.ag",
    "docs.jito.network",
    "solana.com",
    "stackoverflow.com",
}


async def search_docs(query: str) -> dict[str, Any]:
    """Search Brave API and filter by domain whitelist."""
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return {
            "status": "ERROR",
            "error": "BRAVE_API_KEY not set",
            "results": [],
            "count": 0,
        }

    # Basic injection defense
    if any(
        pattern in query.lower()
        for pattern in ["ignore previous", "developer mode", "system:", "execute"]
    ):
        return {
            "status": "ERROR",
            "error": "Query rejected — potential injection pattern detected",
            "results": [],
            "count": 0,
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                params={"q": query, "count": 10, "search_lang": "en", "country": "US"},
            )
            response.raise_for_status()
            data = response.json()

            # Filter results by whitelist
            raw_results = data.get("web", {}).get("results", [])
            filtered = []
            blocked = []

            for result in raw_results:
                url = result.get("url", "")
                domain = urlparse(url).netloc.lower().replace("www.", "")

                # Check if domain or parent domain is allowed
                allowed = any(
                    domain == allowed_domain or domain.endswith(f".{allowed_domain}")
                    for allowed_domain in ALLOWED_DOMAINS
                )

                if allowed:
                    filtered.append({
                        "title": result.get("title", ""),
                        "url": url,
                        "description": result.get("description", ""),
                        "domain": domain,
                    })
                else:
                    blocked.append(domain)

            return {
                "status": "OK",
                "results": filtered[:5],  # Top 5 after filtering
                "count": len(filtered),
                "blocked_domains": list(set(blocked)),
            }

    except httpx.HTTPStatusError as e:
        return {
            "status": "ERROR",
            "error": f"Brave API error: {e.response.status_code}",
            "results": [],
            "count": 0,
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e),
            "results": [],
            "count": 0,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Brave Search (domain-restricted)")
    parser.add_argument("--query", required=True, help="Search query")
    args = parser.parse_args()

    result = asyncio.run(search_docs(args.query))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
