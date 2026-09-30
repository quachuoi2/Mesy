"""Builds each data repo's targets from its sync.ini."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from src.config import (
    DEFAULT_FILE_PATTERNS,
    DEFAULT_SYNC_MODE,
    SETTING_DEFAULTS,
    get_mesy_settings,
    load_sync_cfg,
    load_targets,
)
from src.targets.base import SyncTarget
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.codex import CodexTarget
from src.targets.generic import GenericTarget


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


def _codex_target(target_def: dict, target_dir: Path, repo_base: Path, sync_mode: str) -> SyncTarget:
    return CodexTarget(
        name=target_def["name"],
        src=target_def["source"],
        target_dir=target_dir,
        repo_base=repo_base,
        sync_mode=sync_mode,
    )


def _claude_code_target(target_def: dict, target_dir: Path, repo_base: Path, sync_mode: str) -> SyncTarget:
    return ClaudeCodeTarget(
        src=target_def["source"],
        target_dir=target_dir,
        repo_base=repo_base,
        sync_mode=sync_mode,
    )


def _generic_target(target_def: dict, target_dir: Path, repo_base: Path, sync_mode: str) -> SyncTarget:
    return GenericTarget(
        name=target_def["name"],
        src=target_def["source"],
        target_dir=target_dir,
        repo_base=repo_base,
        file_patterns=target_def.get("file_patterns", list(DEFAULT_FILE_PATTERNS)),
        exclude=target_def.get("exclude", []),
        sync_mode=sync_mode,
    )


_BUILDERS = {
    "codex": _codex_target,
    "claude-code": _claude_code_target,
    "generic": _generic_target,
}


def build_targets(target_defs: list[dict], repo_base: Path) -> list[SyncTarget]:
    """Instantiate SyncTarget objects from config definitions.

    Args:
        target_defs: parsed target definitions from load_targets().
        repo_base: the data repo root — passed to each target so it can
            compute stage dirs and hash file paths without touching globals.
    """
    targets = []
    for target_def in target_defs:
        target_dir = repo_base / target_def["repo_subdir"]
        sync_mode = target_def.get("sync_mode", DEFAULT_SYNC_MODE)
        builder = _BUILDERS[target_def["type"]]
        targets.append(builder(target_def, target_dir, repo_base, sync_mode))

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
