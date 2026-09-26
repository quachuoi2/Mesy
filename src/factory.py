"""Builds each data repo's targets from its sync.conf."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from src.config import get_mesy_settings, load_config, load_targets
from src.targets.base import SyncTarget
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.codex import CodexTarget
from src.targets.generic import GenericTarget

log = logging.getLogger("mesy")


@dataclass
class RepoContext:
    """Bundles everything the daemon needs to manage one data repo."""

    name: str
    data_dir: Path
    targets: list[SyncTarget] = field(default_factory=list)
    stage_dirs: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)


def build_repo_contexts(repo_list: list[dict]) -> list[RepoContext]:
    """Build RepoContext objects from a list of repo dicts (from load_repos())."""
    return [_build_context(repo_info) for repo_info in repo_list]


def _build_context(repo_info: dict) -> RepoContext:
    data_dir = repo_info["path"]
    cfg = load_config(data_dir)
    settings = get_mesy_settings(cfg)
    _check_timings(repo_info["name"], settings)
    targets = build_targets(load_targets(cfg), data_dir)
    return RepoContext(
        name=repo_info["name"],
        data_dir=data_dir,
        targets=targets,
        stage_dirs=get_all_stage_dirs(targets),
        settings=settings,
    )


def _check_timings(repo_name: str, settings: dict) -> None:
    for key in ("debounce_seconds", "pull_interval_seconds", "scan_interval_seconds"):
        try:
            value = float(settings[key])
        except (TypeError, ValueError):
            raise ValueError(f"[{repo_name}] {key} must be a number, got {settings[key]!r}") from None
        # nan passes every comparison as False, which would stop the periodic scan forever.
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"[{repo_name}] {key} must be a finite positive number, got {settings[key]!r}")


def _codex_target(tdef: dict, repo_dir: Path, data_dir: Path, sync_mode: str) -> SyncTarget:
    return CodexTarget(
        name=tdef["name"],
        source=tdef["source"],
        repo_dir=repo_dir,
        data_dir=data_dir,
        sync_mode=sync_mode,
    )


def _claude_code_target(tdef: dict, repo_dir: Path, data_dir: Path, sync_mode: str) -> SyncTarget:
    return ClaudeCodeTarget(
        source=tdef["source"],
        repo_dir=repo_dir,
        data_dir=data_dir,
        sync_mode=sync_mode,
    )


def _generic_target(tdef: dict, repo_dir: Path, data_dir: Path, sync_mode: str) -> SyncTarget:
    return GenericTarget(
        name=tdef["name"],
        source=tdef["source"],
        repo_dir=repo_dir,
        data_dir=data_dir,
        file_patterns=tdef.get("file_patterns", ["*.md"]),
        exclude=tdef.get("exclude", []),
        sync_mode=sync_mode,
    )


_BUILDERS = {
    "codex": _codex_target,
    "claude-code": _claude_code_target,
    "generic": _generic_target,
}


def build_targets(target_defs: list[dict], data_dir: Path) -> list[SyncTarget]:
    """Instantiate SyncTarget objects from config definitions.

    Args:
        target_defs: parsed target definitions from load_targets().
        data_dir: the data repo root — passed to each target so it can
            compute stage dirs and hash file paths without touching globals.
    """
    targets = []
    for tdef in target_defs:
        repo_dir = data_dir / tdef["repo_subdir"]
        sync_mode = tdef.get("sync_mode", "symlink")
        builder = _BUILDERS.get(tdef["type"])
        if builder is None:
            log.warning("Unknown target type: %s", tdef["type"])
            continue
        targets.append(builder(tdef, repo_dir, data_dir, sync_mode))
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
