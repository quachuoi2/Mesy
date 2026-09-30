"""Generic sync target — flat symlink or copy mode for OpenClaw and future tools."""

from __future__ import annotations

import filecmp
import fnmatch
import logging
import shutil
from pathlib import Path

from src.targets._merge import merge_conflicting_file
from src.sync_state import SyncState
from src.targets._migration import FilePair, ensure_real_file
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

_LAST_SYNC_MODE_KEY = "__last_sync_mode__"


class GenericTarget(SyncTarget):

    def __init__(
        self,
        name: str,
        src: Path,
        target_dir: Path,
        repo_base: Path | None = None,
        file_patterns: list[str] | None = None,
        exclude: list[str] | None = None,
        sync_mode: str = "symlink",
    ):
        super().__init__(
            name=name,
            src=src,
            target_dir=target_dir,
            repo_base=repo_base,
            file_patterns=file_patterns,
            exclude=exclude,
            sync_mode=sync_mode,
        )
        # Each copy-mode target gets its own SyncState backed by its data repo.
        if self.sync_mode == "copy":
            self._sync_state = SyncState(
                hash_file=self.repo_base / ".mesy_sync_hashes.json"
            )
        else:
            self._sync_state = None

    def setup(self) -> None:
        """Initial migration — delegates to the same three-phase pipeline as scan()."""
        self.scan()

    def scan(self) -> None:
        """Detect new/changed files using the three-phase model."""
        if not self.src.exists():
            return

        self.target_dir.mkdir(parents=True, exist_ok=True)
        self._migrate()
        pairs = self._walk_and_adopt()
        self._sync(pairs)

    def get_stage_dirs(self) -> list[str]:
        """Return the repo_subdir relative to repo_base for this target."""
        try:
            rel = self.target_dir.relative_to(self.repo_base)
            return [str(rel) + "/"]
        except ValueError:
            return [str(self.target_dir) + "/"]

    def _migrate(self) -> None:
        """Hashes stored before a sync_mode change are stale against edits made
        under the other mode; drop them so the next sync re-seeds (source wins)."""
        if self._sync_state is None:
            return

        last_mode = self._sync_state.get_hash(_LAST_SYNC_MODE_KEY)
        if last_mode is not None and last_mode != self.sync_mode:
            log.info(
                "[%s] sync_mode changed from %s to %s — invalidating stale hashes",
                self.name,
                last_mode,
                self.sync_mode,
            )
            self._sync_state.clear_target_hashes(
                exclude_keys={_LAST_SYNC_MODE_KEY}
            )

        # Stored raw (not hashed) so the next run can compare it.
        self._sync_state.set_raw(_LAST_SYNC_MODE_KEY, self.sync_mode)

    def _walk_and_adopt(self) -> list[FilePair]:
        """Walk source and repo dirs recursively; return (source, repo) pairs.

        For copy mode: uses ensure_real_file to fix leftover symlinks on the
        source side before building the pair.
        For symlink mode: symlinks are already-correct and are skipped (they
        stay in place; Phase 3 handles newly-appearing real files).
        Also creates empty dirs in source for repo subdirs that don't exist yet.
        """
        return self._walk_dir(self.src, self.target_dir)

    def _walk_dir(self, src_dir: Path, target_dir: Path) -> list[FilePair]:
        target_dir.mkdir(parents=True, exist_ok=True)
        pairs, src_names = self._walk_src_side(src_dir, target_dir)
        pairs.extend(self._walk_repo_side(src_dir, target_dir, src_names))
        return pairs

    def _walk_src_side(self, src_dir: Path, target_dir: Path) -> tuple[list[FilePair], set[str]]:
        pairs: list[FilePair] = []
        src_names: set[str] = set()
        for item in sorted(src_dir.iterdir()):
            if self._should_exclude(item.name):
                continue
            src_names.add(item.name)

            if item.is_symlink():
                repo_file = target_dir / item.name
                if self.sync_mode == "copy":
                    fallback = repo_file if repo_file.exists() else None
                    if not item.is_dir() and self._matches_patterns(item.name):
                        ensure_real_file(item, repo_fallback=fallback)
                        if item.exists():  # shape-fix succeeded
                            pairs.append(FilePair(src_file=item, repo_file=repo_file))
                elif self._matches_patterns(item.name):
                    resolved_ok = (
                        item.exists() and item.resolve() == repo_file.resolve()
                    )
                    if not resolved_ok and repo_file.exists():
                        item.unlink()
                        item.symlink_to(repo_file)
                        log.info(
                            "[%s] Fixed broken/wrong symlink: %s", self.name, item.name
                        )
                continue

            if item.is_dir():
                pairs.extend(self._walk_dir(item, target_dir / item.name))
                continue

            if not self._matches_patterns(item.name):
                continue

            pairs.append(FilePair(src_file=item, repo_file=target_dir / item.name))
        return pairs, src_names

    def _walk_repo_side(self, src_dir: Path, target_dir: Path, src_names: set[str]) -> list[FilePair]:
        pairs: list[FilePair] = []
        for repo_item in sorted(target_dir.iterdir()):
            if repo_item.name in src_names:
                continue
            if repo_item.is_dir():
                src_subdir = src_dir / repo_item.name
                src_subdir.mkdir(parents=True, exist_ok=True)
                pairs.extend(self._walk_dir(src_subdir, repo_item))
                continue
            if not self._matches_patterns(repo_item.name):
                continue
            src_file = src_dir / repo_item.name
            if src_file.is_symlink():
                if self.sync_mode == "copy":
                    ensure_real_file(src_file, repo_fallback=repo_item)
                    pairs.append(FilePair(src_file=src_file, repo_file=repo_item))
                elif src_file.resolve() != repo_item.resolve():
                    src_file.unlink()
                    src_file.symlink_to(repo_item)
                    log.info(
                        "[%s] Fixed broken symlink: %s -> %s",
                        self.name,
                        src_file.name,
                        repo_item.name,
                    )
                continue
            pairs.append(FilePair(src_file=src_file, repo_file=repo_item))
        return pairs

    def _sync(self, pairs: list[FilePair]) -> None:
        """Reconcile content for all pairs returned from Phase 2."""
        for pair in pairs:
            if self.sync_mode == "copy":
                self._sync_pair_copy(pair)
            else:
                self._sync_pair_symlink(pair)

    def _sync_pair_symlink(self, pair: FilePair) -> None:
        """Sync a single file pair in symlink mode."""
        src = pair.src_file
        repo = pair.repo_file

        if src.is_symlink():
            # Already correct — nothing to do.
            return

        if src.is_file():
            if not repo.exists():
                shutil.copy2(src, repo)
                log.info("[%s] Copied %s into repo", self.name, src.name)
            elif not filecmp.cmp(src, repo, shallow=False):
                merge_conflicting_file(local=src, repo=repo)
            src.unlink()
            src.symlink_to(repo)
            log.info("[%s] Symlinked %s", self.name, src.name)
        elif repo.exists() and not src.exists():
            src.symlink_to(repo)
            log.info("[%s] Symlinked %s (from repo)", self.name, repo.name)

    def _sync_pair_copy(self, pair: FilePair) -> None:
        """Sync a single file pair in copy mode using three-way hash comparison."""
        src = pair.src_file
        repo = pair.repo_file

        if src.is_file():
            if not repo.exists():
                repo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, repo)
                log.info("[%s] Copied %s -> repo (copy mode)", self.name, src.name)
                key = self._repo_key(repo)
                self._sync_state.set_hash(key, src.read_text())
            elif not filecmp.cmp(src, repo, shallow=False):
                self._three_way_sync(src, repo)
        elif repo.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo, src)
            log.info("[%s] Copied %s -> source (copy mode)", self.name, repo.name)
            key = self._repo_key(repo)
            self._sync_state.set_hash(key, repo.read_text())

    def _should_exclude(self, name: str) -> bool:
        """Check if a file/dir name matches the exclude list."""
        for pat in self.exclude:
            pat = pat.rstrip("/")
            if fnmatch.fnmatch(name, pat):
                return True
        return False

    def _matches_patterns(self, name: str) -> bool:
        """Check if a filename matches any of the file_patterns."""
        return any(fnmatch.fnmatch(name, pat) for pat in self.file_patterns)

    def _repo_key(self, repo_file: Path) -> str:
        """Return the repo_base-relative path as a stable key for hash storage."""
        try:
            return str(repo_file.relative_to(self.repo_base))
        except ValueError:
            return str(repo_file)

    def _three_way_sync(self, src: Path, repo: Path) -> None:
        """Resolve a content difference against the last synced hash; no base hash means source wins."""
        key = self._repo_key(repo)

        src_content = src.read_text()
        repo_content = repo.read_text()

        src_hash = self._sync_state.compute_hash(src_content)
        repo_hash = self._sync_state.compute_hash(repo_content)
        base_hash = self._sync_state.get_hash(key)

        if src_hash == repo_hash:
            if base_hash != src_hash:
                self._sync_state.set_hash(key, src_content)
            return

        if base_hash is None:
            self._take(src, repo, key, src_content, "[%s] First sync, source wins: %s")
            return

        src_changed = src_hash != base_hash
        repo_changed = repo_hash != base_hash

        if src_changed and not repo_changed:
            self._take(src, repo, key, src_content, "[%s] Source updated, copying to repo: %s")
        elif repo_changed and not src_changed:
            self._take(repo, src, key, repo_content, "[%s] Repo updated, copying to source: %s")
        else:
            merge_conflicting_file(local=src, repo=repo)
            merged_content = repo.read_text()
            self._sync_state.set_hash(key, merged_content)

    def _take(self, winner: Path, loser: Path, key: str, content: str, message: str) -> None:
        shutil.copy2(winner, loser)
        log.info(message, self.name, winner.name)
        self._sync_state.set_hash(key, content)
