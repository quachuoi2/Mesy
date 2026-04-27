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
    repo_dir: Path       # <data_repo>/claude/projects/<canonical>


@dataclass
class FilePair:
    """A single file matched between source and repo (GenericTarget)."""

    source_file: Path
    repo_file: Path


# ---------------------------------------------------------------------------
# Shape-fix helpers
# ---------------------------------------------------------------------------


def ensure_real_file(path: Path, repo_fallback: Path | None) -> bool:
    """Convert *path* to a real file if it is a symlink.  Idempotent.

    Preconditions: *path* may be a symlink, a real file, or absent.
    Postcondition: *path* is either a real file or absent — never a symlink.

    Behaviour:
      - Real file or missing: no-op, return False.
      - Symlink whose target exists: copy target content → replace symlink
        with real file (shutil.copy2 for mtime preservation). Return True.
      - Symlink whose target is missing, *repo_fallback* exists: copy
        fallback → real file at *path*. Return True.
      - Symlink whose target is missing, no usable fallback: unlink,
        log warning, return False.  Caller handles absence.

    Never merges content.  Never writes to the repo.
    Content reconciliation (conflict merge) is Phase 3's job.
    """
    if not path.is_symlink():
        return False

    target = path.resolve()
    if target.exists() and target.is_file():
        tmp = path.parent / (path.name + ".mesy_tmp")
        shutil.copy2(target, tmp)
        path.unlink()
        tmp.rename(path)
        log.info("ensure_real_file: materialised symlink -> real file: %s", path.name)
        return True

    # Symlink is dangling (target missing or not a file).
    if repo_fallback is not None and repo_fallback.exists() and repo_fallback.is_file():
        tmp = path.parent / (path.name + ".mesy_tmp")
        shutil.copy2(repo_fallback, tmp)
        path.unlink()
        tmp.rename(path)
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


def ensure_real_dir(path: Path, repo_fallback: Path | None) -> bool:
    """Convert *path* to a real directory if it is a symlink.  Idempotent.

    Preconditions: *path* may be a symlink-to-dir, a real dir, or absent.
    Postcondition: *path* is a real dir or absent.

    Behaviour:
      - Real dir or absent: no-op, return False.
      - Symlink whose target is a dir that exists: create a tmp dir, copy2
        each non-dir file from target into tmp, unlink the symlink, rename
        tmp → path.  Return True.
      - Symlink whose target is missing, *repo_fallback* is an existing dir:
        same copy from fallback.  Return True.
      - Symlink whose target is missing, no usable fallback: unlink, log
        warning, return False.

    Files only — does not recurse into subdirectories (matches the flat-copy
    semantics of the old _copy_dir_files helper).
    Atomic via tmp+rename: a crash mid-copy leaves the original symlink intact.
    Never merges conflicts — that is Phase 3's job.
    """
    if not path.is_symlink():
        return False

    # Determine the source directory to copy from.
    source_dir: Path | None = None
    resolved = path.resolve()
    if resolved.exists() and resolved.is_dir():
        source_dir = resolved
    elif repo_fallback is not None and repo_fallback.exists() and repo_fallback.is_dir():
        source_dir = repo_fallback
        log.warning(
            "ensure_real_dir: symlink target missing for %s, using repo fallback",
            path.name,
        )
    else:
        path.unlink()
        log.warning(
            "ensure_real_dir: dangling symlink with no usable fallback, removed: %s",
            path.name,
        )
        return False

    tmp = path.parent / (path.name + ".mesy_tmp")
    tmp.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        if item.is_dir():
            continue
        shutil.copy2(item, tmp / item.name)

    path.unlink()
    tmp.rename(path)
    log.info("ensure_real_dir: materialised symlink -> real dir: %s", path.name)
    return True
