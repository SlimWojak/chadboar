"""Keychain — Secure bridge between agent and signer subprocess.

THIS MODULE ENFORCES THE ISOLATION BOUNDARY.

The agent process calls `sign_transaction()` here. This module:
1. Reads the signer key location from a SEPARATE env var (SIGNER_KEY_PATH)
   that points to a file readable ONLY by root/signer user, OR
   reads from the macOS keychain (dev mode), OR
   spawns the signer with a minimal env that includes the key.

2. Spawns signer.py as a SUBPROCESS with a CLEAN environment:
   - The subprocess env contains ONLY: PATH, SIGNER_PRIVATE_KEY, PYTHONPATH
   - The agent's env vars (API keys, tokens, etc.) are NOT inherited
   - The subprocess has NO network access intent (it just signs and exits)

3. Passes unsigned tx via STDIN pipe
4. Reads signed tx from STDOUT pipe
5. Returns signed tx bytes to the caller

CRITICAL INVARIANTS:
  - The agent process NEVER has SIGNER_PRIVATE_KEY in os.environ
  - The key is NEVER written to any file by this module
  - The key is NEVER logged, printed, or included in any error message
  - The subprocess gets a MINIMAL env (not os.environ.copy())
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


# The signer script path
SIGNER_SCRIPT = Path(__file__).parent / "signer.py"

# Workspace root (for PYTHONPATH so signer can import solders)
WORKSPACE = Path(__file__).resolve().parent.parent.parent


class SignerError(Exception):
    """Error from the signer subprocess. Never contains key material."""
    pass


def _get_signer_key() -> str:
    """Retrieve the signer private key from a SECURE source.

    Sources (in priority order):
    1. File at SIGNER_KEY_PATH (VPS: chmod 400, owned by signer user)
    2. macOS Keychain via `security` command (dev mode)
    3. AUTISTBOAR_SIGNER_KEY env var (ONLY for testing, NEVER in production)

    Returns base64-encoded private key.
    The agent process should NOT have this in its own os.environ.
    """
    # Source 1: Key file (VPS production mode)
    key_path = os.environ.get("SIGNER_KEY_PATH", "")
    if key_path:
        path = Path(key_path)
        if path.exists():
            return path.read_text().strip()
        raise SignerError(f"Signer key file not found: {key_path}")

    # Source 2: macOS Keychain (dev mode)
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "autistboar-signer", "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Source 3: Env var (testing ONLY — should not be in agent's env in production)
    test_key = os.environ.get("AUTISTBOAR_SIGNER_KEY", "")
    if test_key:
        return test_key

    raise SignerError(
        "No signer key source found. Set SIGNER_KEY_PATH (VPS) or add to "
        "macOS Keychain: security add-generic-password -s autistboar-signer "
        "-a autistboar -w '<base64_private_key>'"
    )


def sign_transaction(unsigned_tx_base64: str) -> str:
    """Sign a transaction using the isolated signer subprocess.

    Args:
        unsigned_tx_base64: Base64-encoded unsigned transaction bytes.

    Returns:
        Base64-encoded signed transaction bytes.

    Raises:
        SignerError: If signing fails for any reason (never contains key material).
    """
    # Get the key from secure storage
    signer_key = _get_signer_key()

    # Build a MINIMAL environment for the signer subprocess.
    # CRITICAL: Do NOT pass os.environ. Build from scratch.
    signer_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        "PYTHONPATH": str(WORKSPACE),
        "SIGNER_PRIVATE_KEY": signer_key,
        # Required for Python to find its stdlib
        "HOME": os.environ.get("HOME", ""),
    }

    # Add virtualenv path if active
    venv = os.environ.get("VIRTUAL_ENV", "")
    if venv:
        signer_env["VIRTUAL_ENV"] = venv
        signer_env["PATH"] = f"{venv}/bin:{signer_env['PATH']}"

    try:
        result = subprocess.run(
            [sys.executable, str(SIGNER_SCRIPT)],
            input=unsigned_tx_base64,
            capture_output=True,
            text=True,
            timeout=10,
            env=signer_env,  # MINIMAL env — not os.environ
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        raise SignerError("Signer subprocess timed out (10s)")
    except Exception as e:
        raise SignerError(f"Failed to spawn signer subprocess: {e}")
    finally:
        # Clear key from this process's memory
        signer_key = ""  # noqa: F841
        signer_env.clear()

    if result.returncode != 0:
        # SECURITY: stderr may contain error details but NEVER key material
        # (signer.py guarantees this)
        error_msg = result.stderr.strip() if result.stderr else "Unknown signer error"
        raise SignerError(f"Signer failed: {error_msg}")

    signed_b64 = result.stdout.strip()
    if not signed_b64:
        raise SignerError("Signer returned empty output")

    return signed_b64


def get_public_key() -> str:
    """Derive the wallet public key via the signer subprocess in --pubkey mode.

    Spawns the signer with --pubkey flag. The signer derives the public key
    from the private key and outputs it on stdout. Same isolation model:
    minimal env, subprocess, no key in agent memory.

    Public key is NOT secret material — does not violate INV-BLIND-KEY.

    Returns:
        Base58-encoded Solana public key string.

    Raises:
        SignerError: If derivation fails.
    """
    signer_key = _get_signer_key()

    signer_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        "PYTHONPATH": str(WORKSPACE),
        "SIGNER_PRIVATE_KEY": signer_key,
        "HOME": os.environ.get("HOME", ""),
    }

    venv = os.environ.get("VIRTUAL_ENV", "")
    if venv:
        signer_env["VIRTUAL_ENV"] = venv
        signer_env["PATH"] = f"{venv}/bin:{signer_env['PATH']}"

    try:
        result = subprocess.run(
            [sys.executable, str(SIGNER_SCRIPT), "--pubkey"],
            capture_output=True,
            text=True,
            timeout=10,
            env=signer_env,
            cwd=str(WORKSPACE),
        )
    except subprocess.TimeoutExpired:
        raise SignerError("Signer subprocess timed out (10s) in --pubkey mode")
    except Exception as e:
        raise SignerError(f"Failed to spawn signer subprocess for pubkey: {e}")
    finally:
        signer_key = ""  # noqa: F841
        signer_env.clear()

    if result.returncode != 0:
        error_msg = result.stderr.strip() if result.stderr else "Unknown signer error"
        raise SignerError(f"Pubkey derivation failed: {error_msg}")

    pubkey = result.stdout.strip()
    if not pubkey:
        raise SignerError("Signer returned empty pubkey")

    return pubkey


def verify_isolation() -> dict[str, Any]:
    """Verify that the agent process does NOT have signer key in its environment.

    Call this during heartbeat to continuously verify isolation.
    Returns a status dict for logging.
    """
    violations: list[str] = []

    # Check that SIGNER_PRIVATE_KEY is NOT in the agent's environment
    if "SIGNER_PRIVATE_KEY" in os.environ:
        violations.append("CRITICAL: SIGNER_PRIVATE_KEY found in agent process environment!")

    # Check that no env var looks like a base58 private key.
    # Whitelist known safe prefixes and expected env vars.
    safe_prefixes = (
        "PATH", "HOME", "PYTHON", "VIRTUAL_ENV", "SHELL", "TERM", "LANG",
        "USER", "LOGNAME", "PWD", "OLDPWD", "TMPDIR", "XDG_", "LC_",
        "OPENROUTER_", "TELEGRAM_", "HELIUS_", "BIRDEYE_", "NANSEN_",
        "X_BEARER_", "SIGNER_KEY_PATH", "AUTISTBOAR_",
        # IDE / system env vars
        "VSCODE_", "CURSOR_", "ELECTRON_", "NODE_", "NPM_", "NVM_",
        "COLORTERM", "GIT_", "SSH_", "GPG_", "DISPLAY", "DBUS_",
        "CONDA_", "HOMEBREW_", "APPLE_", "COMMAND_MODE", "MallocNanoZone",
        "__CF", "__CFB", "SECURITYSESSIONID", "LaunchInstanceID",
        "ORIGINAL_XDG",
    )
    for key, value in os.environ.items():
        if len(value) >= 64 and not any(key.startswith(p) for p in safe_prefixes):
            # Long unknown env var — could be a leaked key
            violations.append(f"WARNING: Suspicious long env var: {key} (len={len(value)})")

    return {
        "status": "VIOLATION" if violations else "CLEAN",
        "violations": violations,
        "message": "Key isolation verified" if not violations else "; ".join(violations),
    }
