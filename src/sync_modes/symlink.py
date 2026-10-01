"""Symlink mode: each local file or folder becomes a link into the repo."""

from __future__ import annotations

import filecmp
import logging
import shutil
from pathlib import Path

from src.conflict import merge_conflicting_file
from src.sync_modes.base import SyncMode, list_files

log = logging.getLogger("mesy")


class SymlinkMode(SyncMode):

    def sync_file(self, local: Path, repo: Path, label: str) -> None:
        if local.is_symlink():
            _relink(local, repo, label)
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

    def sync_dir(self, local: Path, repo: Path, label: str) -> None:
        if local.is_symlink():
            _relink(local, repo, label)
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


def _relink(link: Path, repo: Path, label: str) -> None:
    if not repo.exists() or link.resolve() == repo.resolve():
        return

    link.unlink()
    link.symlink_to(repo, target_is_directory=repo.is_dir())
    log.info("Re-pointed wrong or broken symlink: %s", label)


def merge_into(src: Path, dest_dir: Path) -> None:
    """Copy files missing from dest_dir; merge files that differ. Subfolders included."""
    for f in list_files(src):
        dest_file = dest_dir / f.relative_to(src)
        if not dest_file.exists():
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest_file)
        elif not filecmp.cmp(f, dest_file, shallow=False):
            merge_conflicting_file(local=f, repo=dest_file)


def all_copied(src: Path, dest_dir: Path) -> bool:
    """True when every file under src also exists under dest_dir."""
    return all((dest_dir / f.relative_to(src)).exists() for f in list_files(src))
