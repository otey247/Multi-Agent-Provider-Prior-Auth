"""Load standards-aligned policy packs from disk.

A policy pack is a directory under the policy-packs/ root containing a
``policy_set.json`` file that deserializes into ``models.standards.PolicySet``.

JSON (not YAML) is used deliberately: it loads with the stdlib and adds no new
backend dependency. The PRD allows either format.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from app.models.standards import PolicySet

# backend/app/services/policy_store/loader.py -> repo root is parents[4]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_PACKS_DIR = _REPO_ROOT / "policy-packs"

POLICY_PACK_FILENAME = "policy_set.json"


def policy_packs_dir() -> Path:
    """Resolve the policy-packs directory (env override or repo default)."""
    override = os.getenv("POLICY_PACKS_DIR", "").strip()
    return Path(override) if override else _DEFAULT_PACKS_DIR


def _load_pack_file(path: Path) -> PolicySet | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PolicySet(**data)
    except Exception:  # noqa: BLE001 — a bad pack must never break the pipeline
        return None


@lru_cache(maxsize=1)
def load_policy_packs() -> list[PolicySet]:
    """Load and cache every valid policy pack found on disk.

    Returns an empty list when the directory is missing — the standards layer
    is optional and the review pipeline must degrade gracefully without it.
    """
    base = policy_packs_dir()
    if not base.is_dir():
        return []

    packs: list[PolicySet] = []
    for entry in sorted(base.iterdir()):
        pack_file = entry / POLICY_PACK_FILENAME if entry.is_dir() else None
        if pack_file and pack_file.is_file():
            pack = _load_pack_file(pack_file)
            if pack and pack.policy_set_id:
                packs.append(pack)
    return packs


def get_policy_pack(policy_set_id: str) -> PolicySet | None:
    """Return a single pack by id, or None."""
    for pack in load_policy_packs():
        if pack.policy_set_id == policy_set_id:
            return pack
    return None


def clear_cache() -> None:
    """Drop the in-memory pack cache (used by tests / hot-reload)."""
    load_policy_packs.cache_clear()
