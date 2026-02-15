"""Merkle tree computation — pure functions, no I/O.

Standard binary SHA-256 Merkle tree for anchoring bead batches.
"""

from __future__ import annotations

import hashlib


def _sha256_pair(a: str, b: str) -> str:
    """Hash two hex strings together."""
    combined = bytes.fromhex(a) + bytes.fromhex(b)
    return hashlib.sha256(combined).hexdigest()


def compute_merkle_root(hashes: list[str]) -> str:
    """Compute the Merkle root of a list of hex hash strings.

    Uses standard binary Merkle tree with SHA-256. If the number of leaves
    is odd, the last leaf is duplicated (standard padding).

    Returns "0" * 64 for empty input.
    """
    if not hashes:
        return "0" * 64

    layer = list(hashes)

    while len(layer) > 1:
        next_layer: list[str] = []
        for i in range(0, len(layer), 2):
            if i + 1 < len(layer):
                next_layer.append(_sha256_pair(layer[i], layer[i + 1]))
            else:
                # Odd leaf: duplicate
                next_layer.append(_sha256_pair(layer[i], layer[i]))
        layer = next_layer

    return layer[0]


def build_merkle_tree(hashes: list[str]) -> list[list[str]]:
    """Build full Merkle tree layers for proof generation.

    Returns list of layers from leaves (index 0) to root (last index).
    Each layer is a list of hex hash strings.
    """
    if not hashes:
        return [["0" * 64]]

    layers: list[list[str]] = [list(hashes)]

    while len(layers[-1]) > 1:
        prev = layers[-1]
        next_layer: list[str] = []
        for i in range(0, len(prev), 2):
            if i + 1 < len(prev):
                next_layer.append(_sha256_pair(prev[i], prev[i + 1]))
            else:
                next_layer.append(_sha256_pair(prev[i], prev[i]))
        layers.append(next_layer)

    return layers
