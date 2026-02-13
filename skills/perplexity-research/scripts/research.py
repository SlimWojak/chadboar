#!/usr/bin/env python3
"""
Perplexity Research Script
Queries Perplexity API and returns markdown-formatted research report.
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)


def query_perplexity(prompt: str, model: str = "sonar") -> dict:
    """Query Perplexity API with the given prompt."""
    # Check for PERPLEXITY_KEY first (standard format), fallback to PERPLEXITY_API_KEY
    api_key = os.getenv("PERPLEXITY_KEY") or os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        return {
            "error": "PERPLEXITY_KEY (or PERPLEXITY_API_KEY) not set in environment",
            "success": False
        }
    
    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a research assistant. Provide comprehensive, well-sourced answers in markdown format. Include citations where appropriate."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            return {
                "success": True,
                "content": data["choices"][0]["message"]["content"],
                "model": data.get("model"),
                "citations": data.get("citations", [])
            }
    except httpx.HTTPStatusError as e:
        return {
            "error": f"HTTP {e.response.status_code}: {e.response.text}",
            "success": False
        }
    except Exception as e:
        return {
            "error": str(e),
            "success": False
        }


def main():
    parser = argparse.ArgumentParser(description="Query Perplexity API for research")
    parser.add_argument("--prompt", required=True, help="Research query/prompt")
    parser.add_argument("--model", default="sonar", help="Perplexity model to use")
    parser.add_argument("--output", help="Output file path (optional, prints to stdout if not set)")
    parser.add_argument("--title", help="Report title (defaults to truncated prompt)")
    
    args = parser.parse_args()
    
    # Query Perplexity
    result = query_perplexity(args.prompt, args.model)
    
    if not result["success"]:
        print(json.dumps(result, indent=2), file=sys.stderr)
        sys.exit(1)
    
    # Format as markdown report
    title = args.title or args.prompt[:60]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    
    report = f"""# {title}

**Generated:** {timestamp}  
**Model:** {result.get('model', args.model)}  
**Prompt:** {args.prompt}

---

{result['content']}

---

**Citations:**
"""
    
    if result.get("citations"):
        for i, citation in enumerate(result["citations"], 1):
            report += f"\n{i}. {citation}"
    else:
        report += "\n(No citations provided)"
    
    # Output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)
        print(json.dumps({
            "success": True,
            "file": str(output_path),
            "size": len(report)
        }))
    else:
        print(report)


if __name__ == "__main__":
    main()
