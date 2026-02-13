"""Configuration loader for AutistBoar.

Loads YAML config files from config/ directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parent.parent
CONFIG_DIR = WORKSPACE / "config"


def load_risk_config() -> dict[str, Any]:
    """Load config/risk.yaml."""
    path = CONFIG_DIR / "risk.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load_firehose_config() -> dict[str, Any]:
    """Load config/firehose.yaml."""
    path = CONFIG_DIR / "firehose.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}
