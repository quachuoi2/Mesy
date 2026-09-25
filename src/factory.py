"""Builds each data repo's targets and git sync from its sync.conf."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from src.config import get_mesy_settings, load_config, load_targets
from src.git_sync.base import GitSync
from src.git_sync.isolated import IsolatedGitSync
from src.git_sync.legacy import LegacyGitSync
from src.links import checked_destination
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
    git_sync: GitSync = field(default_factory=LegacyGitSync)


def build_repo_contexts(repo_list: list[dict]) -> list[RepoContext]:
    """Build RepoContext objects from a list of repo dicts (from load_repos())."""
    contexts = [_build_context(repo_info) for repo_info in repo_list]
    validate_ownership(contexts)
    return contexts


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
        git_sync=_git_sync_for(targets),
    )


def validate_ownership(contexts: list[RepoContext]) -> None:
    """Reject targets claiming the same local path or overlapping destinations."""
    claims: list[tuple[Path, SyncTarget]] = []
    destinations: list[tuple[Path, SyncTarget]] = []
    for ctx in contexts:
        for target in ctx.targets:
            _claim_sources(target, claims)
            _claim_destination(target, destinations)
    _check_source_destination(claims, destinations)


def _check_timings(repo_name: str, settings: dict) -> None:
    for key in ("debounce_seconds", "pull_interval_seconds", "scan_interval_seconds"):
        try:
            value = float(settings[key])
        except (TypeError, ValueError):
            raise ValueError(f"[{repo_name}] {key} must be a number, got {settings[key]!r}") from None
        # nan passes every comparison as False, which would stop the periodic scan forever.
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"[{repo_name}] {key} must be a finite positive number, got {settings[key]!r}")


def _git_sync_for(targets: list[SyncTarget]) -> GitSync:
    if any(isinstance(t, CodexTarget) for t in targets):
        return IsolatedGitSync()
    return LegacyGitSync()


def _codex_target(tdef: dict, repo_dir: Path, data_dir: Path, sync_mode: str) -> SyncTarget:
    return CodexTarget(
        name=tdef["name"], source=tdef["source"], repo_dir=repo_dir,
        data_dir=data_dir, sync_mode=sync_mode,
        **{key: tdef[key] for key in ("skills", "skills_source", "export_memories", "device_id", "export_settle_seconds") if key in tdef},
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
        checked_destination(data_dir, repo_dir)
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


def _claim_sources(target: SyncTarget, claims: list[tuple[Path, SyncTarget]]) -> None:
    for source in _claimed_sources(target):
        # Do not resolve the final link: it may already point into the repo.
        path = source.parent.resolve() / source.name
        for previous, owner in claims:
            if _overlaps(path, previous):
                raise ValueError(f"Overlapping sources: {owner.name} ({previous}) and {target.name} ({path})")
        claims.append((path, target))


def _claim_destination(target: SyncTarget, destinations: list[tuple[Path, SyncTarget]]) -> None:
    destination = target.repo_dir.resolve()
    for previous, owner in destinations:
        if _overlaps(destination, previous):
            raise ValueError(f"Overlapping destinations: {owner.name} and {target.name}")
    destinations.append((destination, target))


def _check_source_destination(claims: list[tuple[Path, SyncTarget]],
                              destinations: list[tuple[Path, SyncTarget]]) -> None:
    for source, owner in claims:
        for destination, other in destinations:
            if _overlaps(source, destination):
                if isinstance(owner, CodexTarget) or isinstance(other, CodexTarget):
                    raise ValueError(f"Source/destination overlap: {source} and {destination}")


def _overlaps(path: Path, other: Path) -> bool:
    return path == other or path in other.parents or other in path.parents


def _claimed_sources(target: SyncTarget) -> list[Path]:
    # Not get_source_paths(): Codex only reads its memories folder, and claiming
    # it would reject configurations that work today.
    if isinstance(target, CodexTarget):
        return [source for source, _, _ in target.bindings()]
    return [target.source]
