"""Narrative age tracking — persist first detection times."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class NarrativeTracker:
    """Track when tokens are first detected for age calculation."""
    
    def __init__(self, cache_path: Path = Path("state/narrative_cache.json")):
        self.cache_path = cache_path
        self._cache = self._load()
    
    def _load(self) -> dict[str, Any]:
        """Load cache from disk."""
        if not self.cache_path.exists():
            return {"tokens": {}}
        with open(self.cache_path, 'r') as f:
            return json.load(f)
    
    def _save(self) -> None:
        """Save cache to disk."""
        with open(self.cache_path, 'w') as f:
            json.dump(self._cache, f, indent=2)
    
    def record_detection(self, token_mint: str) -> None:
        """Record first detection time for a token."""
        if token_mint not in self._cache["tokens"]:
            self._cache["tokens"][token_mint] = {
                "first_seen": datetime.utcnow().isoformat(),
                "last_seen": datetime.utcnow().isoformat(),
            }
        else:
            self._cache["tokens"][token_mint]["last_seen"] = datetime.utcnow().isoformat()
        self._save()
    
    def get_age_minutes(self, token_mint: str) -> int:
        """Get age in minutes since first detection. Returns 0 if not seen."""
        if token_mint not in self._cache["tokens"]:
            return 0
        
        first_seen = datetime.fromisoformat(self._cache["tokens"][token_mint]["first_seen"])
        age = (datetime.utcnow() - first_seen).total_seconds() / 60
        return int(age)
    
    def cleanup_old(self, max_age_hours: int = 24) -> None:
        """Remove tokens not seen in max_age_hours."""
        cutoff = datetime.utcnow().timestamp() - (max_age_hours * 3600)
        to_remove = []
        
        for mint, data in self._cache["tokens"].items():
            last_seen = datetime.fromisoformat(data["last_seen"])
            if last_seen.timestamp() < cutoff:
                to_remove.append(mint)
        
        for mint in to_remove:
            del self._cache["tokens"][mint]
        
        if to_remove:
            self._save()
