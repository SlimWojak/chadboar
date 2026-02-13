"""LLM Utilities — xAI Grok API wrapper for ChadBoar.

Provides a unified interface for calling Grok 4.1 FAST with high reasoning.
Used by heartbeat_runner.py for alpha override decisions.

Environment:
    XAI_API_KEY: xAI API key (required)
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


# Default model — Grok 4.1 FAST with reasoning
DEFAULT_MODEL = "grok-4-1-fast-reasoning"
XAI_BASE_URL = "https://api.x.ai/v1"


async def call_grok(
    prompt: str,
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call xAI Grok API for alpha decisions.

    Args:
        prompt: User message / signal summary
        system_prompt: System context (ChadBoar persona)
        model: xAI model identifier
        max_tokens: Max response tokens
        temperature: Sampling temperature (low = deterministic)
        timeout: Request timeout in seconds

    Returns:
        Dict with status, content, model, usage info
    """
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        return {
            "status": "ERROR",
            "error": "XAI_API_KEY not set in environment",
            "content": "",
        }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{XAI_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            return {
                "status": "OK",
                "content": content,
                "model": data.get("model", model),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "reasoning_tokens": usage.get("reasoning_tokens", 0),
                },
            }

    except httpx.HTTPStatusError as e:
        return {
            "status": "ERROR",
            "error": f"xAI API error: {e.response.status_code} — {e.response.text[:200]}",
            "content": "",
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error": f"xAI call failed: {e}",
            "content": "",
        }


def call_grok_sync(
    prompt: str,
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Synchronous wrapper for call_grok. For CLI usage."""
    import asyncio
    return asyncio.run(call_grok(prompt, system_prompt, model, max_tokens, temperature))


# Quick CLI test
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    result = call_grok_sync(
        prompt="Say OINK if you're alive. One word only.",
        system_prompt="You are ChadBoar, a degen trading bot. Be brief.",
    )
    print(json.dumps(result, indent=2))
