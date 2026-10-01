"""Copy mode: local and repo keep separate copies, reconciled against the last synced version."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.conflict import merge_conflicting_file
from src.sync_modes.base import SyncMode, list_files
from src.sync_state import SyncState

TMP_SUFFIX = ".mesy_tmp"
log = logging.getLogger("mesy")


class CopyMode(SyncMode):

    def __init__(self, sync_state: SyncState, repo_base: Path):
        self.sync_state = sync_state
        self.repo_base = repo_base

    def sync_file(self, local: Path, repo: Path, label: str) -> None:
        if local.is_symlink():
            ensure_real_file(local, repo_fallback=repo)

        repo_key = self._repo_key(repo)

        if local.is_file() and repo.is_file():
            self._three_way_sync(local, repo, repo_key, label)
        elif local.is_file():
            self._take(local, repo, repo_key, "Copied %s into repo", label)
        elif repo.is_file():
            self._take(repo, local, repo_key, "Copied %s to local", label)

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

    def _three_way_sync(self, local: Path, repo: Path, repo_key: str, label: str) -> None:
        """Resolve a difference against the last synced version; no stored version means local wins."""
        local_content = _read(local)
        repo_content = _read(repo)

        local_hash = self.sync_state.compute_hash(local_content)
        repo_hash = self.sync_state.compute_hash(repo_content)
        base_hash = self.sync_state.get_hash(repo_key)

        if local_hash == repo_hash:
            if base_hash != local_hash:
                self.sync_state.set_synced(repo_key, local_content)
            return

        if base_hash is None or repo_hash == base_hash:
            self._take(local, repo, repo_key, "Local updated, copied to repo: %s", label)
        elif local_hash == base_hash:
            self._take(repo, local, repo_key, "Repo updated, copied to local: %s", label)
        else:
            merge_conflicting_file(local=local, repo=repo, base_text=self.sync_state.get_text(repo_key))
            self.sync_state.set_synced(repo_key, _read(repo))

    def _take(self, winner: Path, loser: Path, repo_key: str, message: str, label: str) -> None:
        loser.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(winner, loser)
        log.info(message, label)

        self.sync_state.set_synced(repo_key, _read(winner))


def _read(path: Path) -> str:
    # Every file in a synced folder is hashed, not only *.md; a binary file must not stop the scan.
    return path.read_text(errors="replace")


def ensure_real_file(path: Path, repo_fallback: Path | None) -> bool:
    """Replace a file symlink at *path* with a real copy.  Idempotent.

    Copies the link target, or *repo_fallback* when the target is missing.
    Returns True when a real file was written.  A dangling link with no usable
    fallback is removed and False returned.  Never merges content and never
    writes to the repo; merging is left to ``_three_way_sync``.
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


def ensure_real_dir(path: Path, repo_fallback: Path | None) -> bool:
    """Replace a directory symlink at *path* with a real directory.  Idempotent.

    Copies the link target's files, or *repo_fallback*'s when the target is
    missing.  Returns True when a real directory was written.  A dangling link
    with no usable fallback is removed and False returned.

    Files only — does not recurse into subdirectories.
    Atomic via tmp+rename: a crash mid-copy leaves the original symlink intact.
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

    tmp = path.parent / (path.name + TMP_SUFFIX)
    tmp.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        if item.is_dir():
            continue
        shutil.copy2(item, tmp / item.name)

    path.unlink()
    tmp.rename(path)
    log.info("ensure_real_dir: materialised symlink -> real dir: %s", path.name)
    return True


def _replace_with_copy(path: Path, src: Path) -> None:
    tmp = path.parent / (path.name + TMP_SUFFIX)
    shutil.copy2(src, tmp)

    path.unlink()
    tmp.rename(path)


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
