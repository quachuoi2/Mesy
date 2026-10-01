"""Builds each data repo's targets from its sync.ini."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from src.config import (
    DEFAULT_SYNC_MODE,
    SETTING_DEFAULTS,
    get_mesy_settings,
    load_sync_cfg,
    load_targets,
)
from src.sync_modes.base import SyncMode
from src.sync_modes.copy import CopyMode
from src.sync_modes.symlink import SymlinkMode
from src.sync_state import SyncState
from src.targets.base import SyncTarget
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.codex import CodexTarget
from src.targets.generic import GenericTarget

HASH_FILE_NAME = ".mesy_sync_hashes.json"
TEXT_FILE_NAME = "mesy_sync_texts.json"


@dataclass
class RepoCtx:
    """Bundles everything the daemon needs to manage one data repo."""

    name: str
    repo_base: Path
    targets: list[SyncTarget] = field(default_factory=list)
    stage_dirs: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)


def build_repo_ctxs(repo_list: list[dict]) -> list[RepoCtx]:
    """Build RepoCtx objects from a list of repo dicts (from load_repos())."""
    return [_build_ctx(repo_info) for repo_info in repo_list]


def _build_ctx(repo_info: dict) -> RepoCtx:
    repo_base = repo_info["path"]
    sync_cfg = load_sync_cfg(repo_base)

    settings = get_mesy_settings(sync_cfg)
    _check_timings(repo_info["name"], settings)

    targets = build_targets(load_targets(sync_cfg), repo_base)
    return RepoCtx(
        name=repo_info["name"],
        repo_base=repo_base,
        targets=targets,
        stage_dirs=get_all_stage_dirs(targets),
        settings=settings,
    )


def _check_timings(repo_name: str, settings: dict) -> None:
    for key in SETTING_DEFAULTS:
        try:
            value = float(settings[key])

        except (TypeError, ValueError):
            raise ValueError(f"[{repo_name}] {key} must be a number, got {settings[key]!r}") from None

        # nan passes every comparison as False, which would stop the periodic scan forever.
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"[{repo_name}] {key} must be a finite positive number, got {settings[key]!r}")


_TARGET_CLASSES = {
    "codex": CodexTarget,
    "claude-code": ClaudeCodeTarget,
    "generic": GenericTarget,
}


def build_modes(repo_base: Path) -> dict[str, SyncMode]:
    """One object per mode for the whole repo: copy-mode targets must share one SyncState,
    or each overwrites the others' hashes in the shared hash file."""
    sync_state = SyncState(
        hash_file_path=repo_base / HASH_FILE_NAME,
        # Inside .git/ so git never commits it, without a .gitignore line in every data repo.
        text_file_path=repo_base / ".git" / TEXT_FILE_NAME,
    )
    return {"symlink": SymlinkMode(), "copy": CopyMode(sync_state, repo_base)}


def build_targets(target_defs: list[dict], repo_base: Path) -> list[SyncTarget]:
    """Instantiate SyncTarget objects from config definitions.

    Args:
        target_defs: parsed target definitions from load_targets().
        repo_base: the data repo root — passed to each target so it can
            compute stage dirs and hash file paths without touching globals.
    """
    modes = build_modes(repo_base)

    targets = []
    for target_def in target_defs:
        target_args = {
            "name": target_def["name"],
            "src": target_def["source"],
            "target_dir": repo_base / target_def["repo_subdir"],
            "mode": modes[target_def.get("sync_mode", DEFAULT_SYNC_MODE)],
            "repo_base": repo_base,
        }
        if target_def["type"] == "generic":
            target_args["file_patterns"] = target_def.get("file_patterns")
            target_args["exclude"] = target_def.get("exclude")

        targets.append(_TARGET_CLASSES[target_def["type"]](**target_args))

    return targets


def get_all_stage_dirs(targets: list[SyncTarget]) -> list[str]:
    """Collect unique git-add directories from all targets."""
    dirs = []
    seen = set()

    for t in targets:
        for d in t.get_stage_dirs():
            if d not in seen:
                seen.add(d)
                dirs.append(d)

    return dirs
