"""Copy mode: local and repo keep separate copies, reconciled against the last synced version."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.conflict import merge_conflicting_file
from src.sync_modes.base import SyncMode, list_files
from src.sync_state import SyncState
from src.targets._migration import ensure_real_dir, ensure_real_file

log = logging.getLogger("mesy")


class CopyMode(SyncMode):

    def __init__(self, sync_state: SyncState, repo_base: Path):
        self.sync_state = sync_state
        self.repo_base = repo_base

    def sync_file(self, local: Path, repo: Path, label: str) -> None:
        if local.is_symlink():
            ensure_real_file(local, repo_fallback=repo)

        key = self._repo_key(repo)

        if local.is_file() and repo.is_file():
            self._three_way_sync(local, repo, key, label)
        elif local.is_file():
            self._take(local, repo, key, "Copied %s into repo", label)
        elif repo.is_file():
            self._take(repo, local, key, "Copied %s to local", label)

    def sync_dir(self, local: Path, repo: Path, label: str) -> None:
        if local.is_symlink():
            ensure_real_dir(local, repo_fallback=repo)

        local.mkdir(parents=True, exist_ok=True)
        repo.mkdir(parents=True, exist_ok=True)

        rel_paths = {f.relative_to(local) for f in list_files(local)}
        rel_paths |= {f.relative_to(repo) for f in list_files(repo)}

        for rel in sorted(rel_paths):
            self.sync_file(local / rel, repo / rel, f"{label}/{rel.as_posix()}")

    def _repo_key(self, repo: Path) -> str:
        try:
            return str(repo.relative_to(self.repo_base))

        except ValueError:
            return str(repo)

    def _three_way_sync(self, local: Path, repo: Path, key: str, label: str) -> None:
        """Resolve a difference against the last synced version; no stored version means local wins."""
        local_content = _read(local)
        repo_content = _read(repo)

        local_hash = self.sync_state.compute_hash(local_content)
        repo_hash = self.sync_state.compute_hash(repo_content)
        base_hash = self.sync_state.get_hash(key)

        if local_hash == repo_hash:
            if base_hash != local_hash:
                self.sync_state.set_hash(key, local_content)
            return

        if base_hash is None or repo_hash == base_hash:
            self._take(local, repo, key, "Local updated, copied to repo: %s", label)
        elif local_hash == base_hash:
            self._take(repo, local, key, "Repo updated, copied to local: %s", label)
        else:
            merge_conflicting_file(local=local, repo=repo)
            self.sync_state.set_hash(key, _read(repo))

    def _take(self, winner: Path, loser: Path, key: str, message: str, label: str) -> None:
        loser.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(winner, loser)
        log.info(message, label)

        self.sync_state.set_hash(key, _read(winner))


def _read(path: Path) -> str:
    # Every file in a synced folder is hashed, not only *.md; a binary file must not stop the scan.
    return path.read_text(errors="replace")
