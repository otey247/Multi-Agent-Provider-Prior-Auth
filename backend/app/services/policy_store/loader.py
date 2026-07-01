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

# loader.py path: <backend>/app/services/policy_store/loader.py
#   parents[3] = <backend>      -> bundled packs that ship inside the image
#   parents[4] = <repo root>    -> monorepo authoring location (local dev)
_HERE = Path(__file__).resolve()
# Bundled with the backend so it is present in the container image
# (Dockerfile: COPY policy_packs/ ./policy_packs/). This is the canonical dir.
_BUNDLED_PACKS_DIR = _HERE.parents[3] / "policy_packs"
# Repo-root fallback for local monorepo authoring (not shipped in the image).
_REPO_ROOT_PACKS_DIR = _HERE.parents[4] / "policy-packs"

POLICY_PACK_FILENAME = "policy_set.json"


def _candidate_dirs() -> list[Path]:
    """Directories to scan for policy packs, in priority order."""
    override = os.getenv("POLICY_PACKS_DIR", "").strip()
    if override:
        return [Path(override)]
    return [_BUNDLED_PACKS_DIR, _REPO_ROOT_PACKS_DIR]


def policy_packs_dir() -> Path:
    """Primary policy-packs directory (env override or bundled default)."""
    return _candidate_dirs()[0]


def _load_pack_file(path: Path) -> PolicySet | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PolicySet(**data)
    except Exception:  # noqa: BLE001 — a bad pack must never break the pipeline
        return None


@lru_cache(maxsize=1)
def load_policy_packs() -> list[PolicySet]:
    """Load and cache every valid policy pack found on disk.

    Scans the candidate directories in priority order (bundled, then
    repo-root), de-duplicating by ``policy_set_id`` so the shipped copy wins.
    Returns an empty list when no directory exists — the standards layer is
    optional and the review pipeline must degrade gracefully without it.
    """
    packs: list[PolicySet] = []
    seen: set[str] = set()
    for base in _candidate_dirs():
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            pack_file = entry / POLICY_PACK_FILENAME if entry.is_dir() else None
            if pack_file and pack_file.is_file():
                pack = _load_pack_file(pack_file)
                if pack and pack.policy_set_id and pack.policy_set_id not in seen:
                    seen.add(pack.policy_set_id)
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
