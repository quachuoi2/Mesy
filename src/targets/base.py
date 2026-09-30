"""Base class for Mesy sync targets."""

from __future__ import annotations

import filecmp
import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from src.config import DEFAULT_FILE_PATTERNS, DEFAULT_SYNC_MODE
from src.conflict import merge_conflicting_file

log = logging.getLogger("mesy")


class SyncTarget(ABC):
    """Base class for a sync target (e.g. Claude Code, OpenClaw)."""

    name: str
    src: Path          # tool's directory (e.g. ~/.openclaw/workspace)
    target_dir: Path        # subdirectory in the data repo (e.g. AlfredMemory/openclaw/main)
    repo_base: Path        # root of the data repo this target belongs to
    file_patterns: list[str]
    exclude: list[str]

    def __init__(
        self,
        name: str,
        src: Path,
        target_dir: Path,
        repo_base: Path | None = None,
        file_patterns: list[str] | None = None,
        exclude: list[str] | None = None,
        sync_mode: str = DEFAULT_SYNC_MODE,
    ):
        self.name = name
        self.src = src
        self.target_dir = target_dir
        self.repo_base = repo_base if repo_base is not None else target_dir.parent
        self.file_patterns = file_patterns or list(DEFAULT_FILE_PATTERNS)
        self.exclude = exclude or []
        self.sync_mode = sync_mode

    @abstractmethod
    def setup(self) -> None:
        """Initial migration + symlink creation. Called once on first run."""
        ...

    @abstractmethod
    def scan(self) -> None:
        """Detect new files needing symlinks. Called periodically."""
        ...

    @abstractmethod
    def get_stage_dirs(self) -> list[str]:
        """Return git-add paths relative to repo_base for this target."""
        ...

    def _sync_file(self, local: Path, repo: Path, label: str) -> None:
        """Reconcile a single file between local and repo per sync_mode."""
        if self.sync_mode == "copy":
            self._sync_file_copy(local, repo, label)
        else:
            self._sync_file_symlink(local, repo, label)

    def _sync_file_copy(self, local: Path, repo: Path, label: str) -> None:
        if local.is_file():
            if not repo.exists():
                repo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local, repo)
                log.info("Copied %s into repo (copy mode)", label)
            elif not filecmp.cmp(local, repo, shallow=False):
                merge_conflicting_file(local=local, repo=repo)
        elif repo.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo, local)
            log.info("Copied %s -> local (copy mode)", label)

    def _sync_file_symlink(self, local: Path, repo: Path, label: str) -> None:
        if local.is_symlink():
            return

        if local.is_file():
            repo.parent.mkdir(parents=True, exist_ok=True)

            if not repo.exists():
                shutil.copy2(local, repo)
                log.info("Copied %s into repo", label)
            elif not filecmp.cmp(local, repo, shallow=False):
                merge_conflicting_file(local=local, repo=repo)

            local.unlink()
            local.symlink_to(repo)
            log.info("Symlinked %s", label)
        elif repo.exists() and not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            local.symlink_to(repo)
            log.info("Symlinked %s (from repo)", label)

    def _sync_dir(self, local: Path, repo: Path, label: str) -> None:
        """Reconcile a whole directory, subdirectories included, per sync_mode."""
        if self.sync_mode == "copy":
            self._sync_dir_copy(local, repo)
        else:
            self._sync_dir_symlink(local, repo, label)

    def _sync_dir_copy(self, local: Path, repo: Path) -> None:
        local.mkdir(parents=True, exist_ok=True)
        repo.mkdir(parents=True, exist_ok=True)

        merge_into(local, repo)
        _copy_missing(repo, local)

    def _sync_dir_symlink(self, local: Path, repo: Path, label: str) -> None:
        if local.is_symlink():
            return

        if local.is_dir():
            repo.mkdir(parents=True, exist_ok=True)
            merge_into(local, repo)

            if not all_copied(local, repo):
                log.error("Migration verification failed for %s, skipping rmtree", label)
                return

            shutil.rmtree(local)
            local.symlink_to(repo, target_is_directory=True)
            log.info("Adopted %s -> %s", label, repo)
        elif repo.exists() and not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            local.symlink_to(repo, target_is_directory=True)
            log.info("Symlinked %s (from repo)", label)


def _files(root: Path) -> list[Path]:
    return [f for f in root.rglob("*") if f.is_file()]


def merge_into(src: Path, dest_dir: Path) -> None:
    """Copy files missing from dest_dir; merge files that differ. Subfolders included."""
    for f in _files(src):
        dest_file = dest_dir / f.relative_to(src)
        if not dest_file.exists():
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest_file)
        elif not filecmp.cmp(f, dest_file, shallow=False):
            merge_conflicting_file(local=f, repo=dest_file)


def all_copied(src: Path, dest_dir: Path) -> bool:
    """True when every file under src also exists under dest_dir."""
    return all((dest_dir / f.relative_to(src)).exists() for f in _files(src))


def _copy_missing(src: Path, dest_dir: Path) -> None:
    for f in _files(src):
        dest_file = dest_dir / f.relative_to(src)
        if not dest_file.exists():
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest_file)
