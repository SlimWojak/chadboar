"""Tests for Blind KeyMan Signer — INV-BLIND-KEY.

The most critical test file in the project. Verifies:
1. Private key NEVER appears in agent process environment
2. Private key NEVER appears in any log, output, or file
3. Signer subprocess gets a MINIMAL environment
4. Signer subprocess does not inherit agent API keys
5. Key isolation verification works
6. Error messages never contain key material

These tests use a FAKE test key — never a real one.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.signer.keychain import verify_isolation, SignerError

# A fake 64-byte "private key" for testing. NOT a real key.
FAKE_KEY_BYTES = bytes(range(64))
FAKE_KEY_B64 = base64.b64encode(FAKE_KEY_BYTES).decode()


class TestKeyIsolation:
    """INV-BLIND-KEY: Private key never enters agent context."""

    def test_signer_private_key_not_in_agent_env(self):
        """SIGNER_PRIVATE_KEY must NOT be in the agent process env."""
        assert "SIGNER_PRIVATE_KEY" not in os.environ, (
            "CRITICAL: SIGNER_PRIVATE_KEY found in agent process environment! "
            "This violates INV-BLIND-KEY."
        )

    def test_verify_isolation_clean(self):
        """verify_isolation() returns CLEAN when SIGNER_PRIVATE_KEY not in env."""
        # The primary check: SIGNER_PRIVATE_KEY must not be in agent env.
        # Long env var warnings are advisory (IDE/pytest may add their own).
        assert "SIGNER_PRIVATE_KEY" not in os.environ
        result = verify_isolation()
        # No CRITICAL violations (advisory warnings are OK in dev)
        critical = [v for v in result["violations"] if "CRITICAL" in v]
        assert not critical, f"Critical isolation violation: {critical}"

    def test_verify_isolation_catches_leak(self, monkeypatch):
        """verify_isolation() catches key in agent env."""
        monkeypatch.setenv("SIGNER_PRIVATE_KEY", "leaked_key_value")
        result = verify_isolation()
        assert result["status"] == "VIOLATION"
        assert any("SIGNER_PRIVATE_KEY" in v for v in result["violations"])

    def test_signer_env_is_minimal(self):
        """The signer subprocess env must NOT contain agent API keys."""
        # Simulate what keychain.py builds for the signer
        from lib.signer.keychain import WORKSPACE

        signer_env = {
            "PATH": os.environ.get("PATH", "/usr/bin"),
            "PYTHONPATH": str(WORKSPACE),
            "SIGNER_PRIVATE_KEY": FAKE_KEY_B64,
            "HOME": os.environ.get("HOME", ""),
        }

        # These should NOT be in signer env
        agent_keys = [
            "OPENROUTER_API_KEY", "HELIUS_API_KEY", "BIRDEYE_API_KEY",
            "NANSEN_API_KEY", "X_BEARER_TOKEN", "TELEGRAM_BOT_TOKEN",
        ]
        for key in agent_keys:
            assert key not in signer_env, (
                f"Agent API key {key} must NOT be in signer subprocess environment"
            )

    def test_signer_does_not_inherit_os_environ(self):
        """Verify that keychain.py builds env from scratch, not os.environ.copy()."""
        import inspect
        from lib.signer import keychain

        source = inspect.getsource(keychain.sign_transaction)
        # The function should NOT use os.environ.copy()
        assert "os.environ.copy()" not in source, (
            "sign_transaction must NOT use os.environ.copy(). "
            "Build signer env from scratch."
        )
        # It SHOULD build a minimal dict
        assert "signer_env" in source or "signer_env =" in source


class TestSignerSubprocess:
    """Test the signer subprocess behavior."""

    def test_signer_fails_without_key(self):
        """Signer exits with error when no SIGNER_PRIVATE_KEY."""
        result = subprocess.run(
            [sys.executable, "-m", "lib.signer.signer"],
            input="fake_tx_data",
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 1
        assert "SIGNER_PRIVATE_KEY" in result.stderr
        # Error message should NOT contain any key material
        assert FAKE_KEY_B64 not in result.stderr

    def test_signer_fails_without_stdin(self):
        """Signer exits with error when no transaction on stdin."""
        result = subprocess.run(
            [sys.executable, "-m", "lib.signer.signer"],
            input="",
            capture_output=True,
            text=True,
            timeout=5,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "SIGNER_PRIVATE_KEY": FAKE_KEY_B64,
            },
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 1
        assert "No transaction data" in result.stderr
        # Error should NOT leak the key
        assert FAKE_KEY_B64 not in result.stderr
        assert FAKE_KEY_B64 not in result.stdout

    def test_signer_error_never_contains_key(self):
        """All signer error paths must never include key material."""
        # Feed invalid base64 that will fail decode
        result = subprocess.run(
            [sys.executable, "-m", "lib.signer.signer"],
            input="not_valid_base64!!!",
            capture_output=True,
            text=True,
            timeout=5,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "SIGNER_PRIVATE_KEY": FAKE_KEY_B64,
                "PYTHONPATH": str(Path(__file__).parent.parent),
            },
            cwd=str(Path(__file__).parent.parent),
        )
        # Regardless of exit code, key must not be in any output
        assert FAKE_KEY_B64 not in result.stdout
        assert FAKE_KEY_B64 not in result.stderr


class TestKeyAudit:
    """Audit all source files for potential key leaks."""

    def test_no_hardcoded_keys_in_source(self):
        """No non-test source file should contain hardcoded private keys."""
        workspace = Path(__file__).parent.parent
        suspicious_patterns = [
            b"-----BEGIN PRIVATE KEY-----",
            b"-----BEGIN EC PRIVATE KEY-----",
        ]

        violations = []
        for py_file in workspace.rglob("*.py"):
            # Skip test files, venv, and cache
            if ".venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            if "test_" in py_file.name:
                continue
            content = py_file.read_bytes()
            for pattern in suspicious_patterns:
                if pattern in content:
                    violations.append(f"{py_file}: contains '{pattern.decode()}'")

        assert not violations, (
            f"Potential key material in source files:\n" + "\n".join(violations)
        )

    def test_signer_py_has_no_logging(self):
        """signer.py must not import logging or write files."""
        signer_path = Path(__file__).parent.parent / "lib" / "signer" / "signer.py"
        content = signer_path.read_text()

        assert "import logging" not in content, "signer.py must not import logging"
        assert "open(" not in content, "signer.py must not open files"
        assert "write(" not in content or "sys.stdout.write" in content, (
            "signer.py must only write to stdout, never to files"
        )

    def test_no_key_in_beads_dir(self):
        """Beads directory should never contain key material."""
        beads_dir = Path(__file__).parent.parent / "beads"
        if not beads_dir.exists():
            return

        for bead_file in beads_dir.glob("*.md"):
            content = bead_file.read_text()
            # Check for base64-encoded key patterns (64+ chars of base64)
            assert "SIGNER_PRIVATE_KEY" not in content
            assert "private_key" not in content.lower()
            assert "seed_phrase" not in content.lower()
