"""File locking utilities for safe concurrent state updates."""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


@contextmanager
def exclusive_file_lock(path: Path) -> Generator[None, None, None]:
    """Context manager for exclusive file locking.
    
    Usage:
        with exclusive_file_lock(Path("state/state.json")):
            # Read, modify, write
            pass
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(lock_path, "w") as lock_file:
        try:
            # Acquire exclusive lock (blocks if another process holds it)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            # Release lock
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def safe_read_json(path: Path) -> dict[str, Any]:
    """Read JSON with file locking and auto-recovery from backup on corruption.
    
    If the file is corrupted, attempts to restore from .bak backup.
    """
    with exclusive_file_lock(path):
        if not path.exists():
            return {}
        
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            # Corrupted JSON — attempt recovery from backup
            backup_path = path.with_suffix(path.suffix + ".bak")
            if backup_path.exists():
                import shutil
                print(f"⚠️  Corrupted state detected: {path}")
                print(f"   Restoring from backup: {backup_path}")
                shutil.copy(backup_path, path)
                
                # Retry read
                with open(path, 'r') as f:
                    return json.load(f)
            else:
                # No backup available — re-raise original error
                raise e


def safe_write_json(path: Path, data: dict[str, Any], indent: int = 2) -> None:
    """Write JSON with file locking and atomic tmp+rename pattern.
    
    Also creates a .bak backup before overwriting existing file.
    """
    with exclusive_file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create backup if file exists
        backup_path = path.with_suffix(path.suffix + ".bak")
        if path.exists():
            import shutil
            shutil.copy(path, backup_path)
        
        # Write to temporary file first
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=indent)
        
        # Atomic rename
        tmp_path.rename(path)


def safe_update_json(
    path: Path,
    update_fn: Any,  # Callable[[dict], dict]
    indent: int = 2,
) -> dict[str, Any]:
    """Atomically read-modify-write JSON.
    
    Args:
        path: Path to JSON file
        update_fn: Function that takes current state and returns updated state
        indent: JSON indent level
    
    Returns:
        Updated state
    """
    with exclusive_file_lock(path):
        # Read current state
        if path.exists():
            with open(path, 'r') as f:
                current = json.load(f)
        else:
            current = {}
        
        # Apply update
        updated = update_fn(current)
        
        # Write back
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(updated, f, indent=indent)
        
        return updated
