"""Shared migration helpers for Phase 2 shape-fixing across all SyncTargets.

These helpers convert symlinks to real files/dirs so that Phase 3 content
reconciliation can assume clean, mode-appropriate shapes.  They are idempotent
and never write to the repo side.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("mesy")


# ---------------------------------------------------------------------------
# Pair dataclasses (Phase 2 output, Phase 3 input)
# ---------------------------------------------------------------------------


@dataclass
class ProjectPair:
    """A mapped Claude Code project with its local and repo paths."""

    local_memory: Path   # ~/.claude/projects/<raw>/memory
    local_claude_md: Path  # ~/.claude/projects/<raw>/CLAUDE.md
    canonical_dir: Path       # <data_repo>/claude/projects/<canonical>


@dataclass
class FilePair:
    """A single file matched between source and repo (GenericTarget)."""

    src_file: Path
    repo_file: Path


# ---------------------------------------------------------------------------
# Shape-fix helpers
# ---------------------------------------------------------------------------


def ensure_real_file(path: Path, repo_fallback: Path | None) -> bool:
    """Replace a file symlink at *path* with a real copy.  Idempotent.

    Copies the link target, or *repo_fallback* when the target is missing.
    Returns True when a real file was written.  A dangling link with no usable
    fallback is removed and False returned.  Never merges content and never
    writes to the repo; content reconciliation is Phase 3's job.
    """
    if not path.is_symlink():
        return False

    target = path.resolve()
    if target.exists() and target.is_file():
        _replace_with_copy(path, target)
        log.info("ensure_real_file: materialised symlink -> real file: %s", path.name)
        return True

    if repo_fallback is not None and repo_fallback.exists() and repo_fallback.is_file():
        _replace_with_copy(path, repo_fallback)
        log.info(
            "ensure_real_file: dangling symlink, restored from fallback: %s", path.name
        )
        return True

    path.unlink()
    log.warning(
        "ensure_real_file: dangling symlink with no usable fallback, removed: %s",
        path.name,
    )
    return False


def _replace_with_copy(path: Path, src: Path) -> None:
    tmp = path.parent / (path.name + ".mesy_tmp")
    shutil.copy2(src, tmp)

    path.unlink()
    tmp.rename(path)


def ensure_real_dir(path: Path, repo_fallback: Path | None) -> bool:
    """Replace a directory symlink at *path* with a real directory.  Idempotent.

    Copies the link target's files, or *repo_fallback*'s when the target is
    missing.  Returns True when a real directory was written.  A dangling link
    with no usable fallback is removed and False returned.

    Files only — does not recurse into subdirectories (matches the flat-copy
    semantics of the old _copy_dir_files helper).
    Atomic via tmp+rename: a crash mid-copy leaves the original symlink intact.
    Never merges conflicts — that is Phase 3's job.
    """
    if not path.is_symlink():
        return False

    src_dir = _real_dir_src(path, repo_fallback)
    if src_dir is None:
        path.unlink()
        log.warning(
            "ensure_real_dir: dangling symlink with no usable fallback, removed: %s",
            path.name,
        )
        return False

    tmp = path.parent / (path.name + ".mesy_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        if item.is_dir():
            continue
        shutil.copy2(item, tmp / item.name)

    path.unlink()
    tmp.rename(path)
    log.info("ensure_real_dir: materialised symlink -> real dir: %s", path.name)
    return True


def _real_dir_src(path: Path, repo_fallback: Path | None) -> Path | None:
    resolved = path.resolve()
    if resolved.exists() and resolved.is_dir():
        return resolved

    if repo_fallback is not None and repo_fallback.exists() and repo_fallback.is_dir():
        log.warning(
            "ensure_real_dir: symlink target missing for %s, using repo fallback",
            path.name,
        )
        return repo_fallback

    return None
