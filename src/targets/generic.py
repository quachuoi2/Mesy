"""Generic sync target — flat symlink or copy mode for OpenClaw and future tools."""

from __future__ import annotations

import filecmp
import fnmatch
import logging
import shutil
from pathlib import Path

from src.git_utils import merge_conflicting_file
from src.sync_state import SyncState
from src.targets._migration import FilePair, ensure_real_file
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")

_LAST_SYNC_MODE_KEY = "__last_sync_mode__"


class GenericTarget(SyncTarget):

    def __init__(
        self,
        name: str,
        source: Path,
        repo_dir: Path,
        data_dir: Path | None = None,
        file_patterns: list[str] | None = None,
        exclude: list[str] | None = None,
        sync_mode: str = "symlink",
    ):
        # Infer data_dir from repo_dir when not explicitly provided (backward compat).
        resolved_data_dir = data_dir if data_dir is not None else repo_dir.parent
        super().__init__(
            name=name,
            source=source,
            repo_dir=repo_dir,
            data_dir=resolved_data_dir,
            file_patterns=file_patterns,
            exclude=exclude,
            sync_mode=sync_mode,
        )
        # Each copy-mode target gets its own SyncState backed by its data repo.
        if self.sync_mode == "copy":
            self._sync_state = SyncState(
                hash_file=self.data_dir / ".mesy_sync_hashes.json"
            )
        else:
            self._sync_state = None

    def setup(self) -> None:
        """Initial migration — delegates to the same three-phase pipeline as scan()."""
        self.scan()

    def scan(self) -> None:
        """Detect new/changed files using the three-phase model."""
        if not self.source.exists():
            return

        self.repo_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # PHASE 1: MIGRATION
        # Detect sync_mode change since last run; invalidate stale hashes on
        # copy → other transitions so Phase 3 re-seeds cleanly.
        # ------------------------------------------------------------------
        self._migrate()

        # ------------------------------------------------------------------
        # PHASE 2: ADOPTION
        # Walk source + repo, shape-fix mapped files, build pair list.
        # No content writes beyond shape-fix.
        # ------------------------------------------------------------------
        pairs = self._walk_and_adopt()

        # ------------------------------------------------------------------
        # PHASE 3: SYNC
        # Reconcile content per sync_mode.
        # ------------------------------------------------------------------
        self._sync(pairs)

    def get_stage_dirs(self) -> list[str]:
        """Return the repo_subdir relative to data_dir for this target."""
        try:
            rel = self.repo_dir.relative_to(self.data_dir)
            return [str(rel) + "/"]
        except ValueError:
            return [str(self.repo_dir) + "/"]

    # ======================================================================
    # PHASE 1: MIGRATION
    # Detect mode changes, invalidate stale hashes, persist current mode.
    # ======================================================================

    def _migrate(self) -> None:
        """Detect sync_mode change since last run and invalidate stale hashes.

        If the mode changed away from copy mode (e.g. copy → symlink → copy),
        any stored hashes from a prior copy-mode run are stale relative to
        edits made while in symlink mode.  Invalidating them forces Phase 3
        to re-seed via first-sync logic (source wins) rather than producing
        wrong three-way decisions.

        The current sync_mode is persisted in the SyncState hash file under
        a reserved key so the next run can compare.
        """
        if self._sync_state is None:
            # Symlink mode: no hash tracking, nothing to do.
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

        # Persist current mode for next run's comparison.
        # Store raw string (not hashed) under the reserved key.
        self._sync_state.set_raw(_LAST_SYNC_MODE_KEY, self.sync_mode)

    # ======================================================================
    # PHASE 2: ADOPTION
    # Walk source + repo, shape-fix files, build pair list.
    # ======================================================================

    def _walk_and_adopt(self) -> list[FilePair]:
        """Walk source and repo dirs recursively; return (source, repo) pairs.

        For copy mode: uses ensure_real_file to fix leftover symlinks on the
        source side before building the pair.
        For symlink mode: symlinks are already-correct and are skipped (they
        stay in place; Phase 3 handles newly-appearing real files).
        Also creates empty dirs in source for repo subdirs that don't exist yet.
        """
        return self._walk_dir(self.source, self.repo_dir)

    def _walk_dir(self, source_dir: Path, repo_dir: Path) -> list[FilePair]:
        """Recursive helper for _walk_and_adopt."""
        repo_dir.mkdir(parents=True, exist_ok=True)
        pairs: list[FilePair] = []

        # Collect all names visible in source or repo.
        source_names: set[str] = set()
        for item in sorted(source_dir.iterdir()):
            if self._should_exclude(item.name):
                continue
            source_names.add(item.name)

            if item.is_symlink():
                repo_file = repo_dir / item.name
                if self.sync_mode == "copy":
                    # Phase 2 shape-fix: ensure the source is a real file in copy mode.
                    fallback = repo_file if repo_file.exists() else None
                    if not item.is_dir() and self._matches_patterns(item.name):
                        ensure_real_file(item, repo_fallback=fallback)
                        if item.exists():  # shape-fix succeeded
                            pairs.append(FilePair(source_file=item, repo_file=repo_file))
                    continue
                # symlink mode:
                if self._matches_patterns(item.name):
                    resolved_ok = (
                        item.exists() and item.resolve() == repo_file.resolve()
                    )
                    if not resolved_ok and repo_file.exists():
                        # Broken or wrong symlink — fix it in place (Phase 2 shape-fix).
                        item.unlink()
                        item.symlink_to(repo_file)
                        log.info(
                            "[%s] Fixed broken/wrong symlink: %s", self.name, item.name
                        )
                # Either correct symlink or no repo file — nothing for Phase 3 to do.
                continue

            if item.is_dir():
                # Recurse; this also handles repo-only subdirs below.
                pairs.extend(self._walk_dir(item, repo_dir / item.name))
                continue

            if not self._matches_patterns(item.name):
                continue

            pairs.append(FilePair(source_file=item, repo_file=repo_dir / item.name))

        # Repo → source: add pairs for repo files/dirs not in source.
        for repo_item in sorted(repo_dir.iterdir()):
            if repo_item.name in source_names:
                continue
            if repo_item.is_dir():
                source_subdir = source_dir / repo_item.name
                source_subdir.mkdir(parents=True, exist_ok=True)
                pairs.extend(self._walk_dir(source_subdir, repo_item))
                continue
            if not self._matches_patterns(repo_item.name):
                continue
            source_file = source_dir / repo_item.name
            if source_file.is_symlink():
                # Leftover or broken symlink in source position for a repo file.
                if self.sync_mode == "copy":
                    # Phase 2 shape-fix: convert to real file.
                    ensure_real_file(source_file, repo_fallback=repo_item)
                    pairs.append(FilePair(source_file=source_file, repo_file=repo_item))
                else:
                    # symlink mode: fix broken/wrong symlink so it points at the repo file.
                    if source_file.resolve() != repo_item.resolve():
                        source_file.unlink()
                        source_file.symlink_to(repo_item)
                        log.info(
                            "[%s] Fixed broken symlink: %s -> %s",
                            self.name,
                            source_file.name,
                            repo_item.name,
                        )
                continue
            pairs.append(FilePair(source_file=source_file, repo_file=repo_item))

        return pairs

    # ======================================================================
    # PHASE 3: SYNC
    # Reconcile content per sync_mode.
    # ======================================================================

    def _sync(self, pairs: list[FilePair]) -> None:
        """Reconcile content for all pairs returned from Phase 2."""
        for pair in pairs:
            if self.sync_mode == "copy":
                self._sync_pair_copy(pair)
            else:
                self._sync_pair_symlink(pair)

    def _sync_pair_symlink(self, pair: FilePair) -> None:
        """Sync a single file pair in symlink mode."""
        source = pair.source_file
        repo = pair.repo_file

        if source.is_symlink():
            # Already correct — nothing to do.
            return

        if source.is_file():
            if not repo.exists():
                shutil.copy2(source, repo)
                log.info("[%s] Copied %s into repo", self.name, source.name)
            elif not filecmp.cmp(source, repo, shallow=False):
                merge_conflicting_file(local=source, repo=repo)
            source.unlink()
            source.symlink_to(repo)
            log.info("[%s] Symlinked %s", self.name, source.name)
        elif repo.exists() and not source.exists():
            source.symlink_to(repo)
            log.info("[%s] Symlinked %s (from repo)", self.name, repo.name)

    def _sync_pair_copy(self, pair: FilePair) -> None:
        """Sync a single file pair in copy mode using three-way hash comparison."""
        source = pair.source_file
        repo = pair.repo_file

        if source.is_file():
            if not repo.exists():
                repo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, repo)
                log.info("[%s] Copied %s -> repo (copy mode)", self.name, source.name)
                key = self._repo_key(repo)
                self._sync_state.set_hash(key, source.read_text())
            elif not filecmp.cmp(source, repo, shallow=False):
                self._three_way_sync(source, repo)
        elif repo.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo, source)
            log.info("[%s] Copied %s -> source (copy mode)", self.name, repo.name)
            key = self._repo_key(repo)
            self._sync_state.set_hash(key, repo.read_text())

    # ======================================================================
    # INTERNAL UTILITIES
    # ======================================================================

    def _should_exclude(self, name: str) -> bool:
        """Check if a file/dir name matches the exclude list."""
        for pattern in self.exclude:
            pattern = pattern.rstrip("/")
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _matches_patterns(self, name: str) -> bool:
        """Check if a filename matches any of the file_patterns."""
        return any(fnmatch.fnmatch(name, pat) for pat in self.file_patterns)

    def _repo_key(self, repo_file: Path) -> str:
        """Return the data_dir-relative path as a stable key for hash storage."""
        try:
            return str(repo_file.relative_to(self.data_dir))
        except ValueError:
            return str(repo_file)

    def _three_way_sync(self, source: Path, repo: Path) -> None:
        """Resolve a content difference using three-way hash comparison.

        Cases:
        - source_hash == repo_hash: already identical, just ensure hash is stored.
        - source changed, repo unchanged: copy source → repo.
        - repo changed, source unchanged: copy repo → source.
        - both changed: real conflict, delegate to merge_conflicting_file.
        - no base hash (first sync): source wins, copy source → repo.
        """
        key = self._repo_key(repo)

        source_content = source.read_text()
        repo_content = repo.read_text()

        source_hash = self._sync_state.compute_hash(source_content)
        repo_hash = self._sync_state.compute_hash(repo_content)
        base_hash = self._sync_state.get_hash(key)

        if source_hash == repo_hash:
            if base_hash != source_hash:
                self._sync_state.set_hash(key, source_content)
            return

        if base_hash is None:
            shutil.copy2(source, repo)
            log.info("[%s] First sync, source wins: %s", self.name, source.name)
            self._sync_state.set_hash(key, source_content)
            return

        source_changed = source_hash != base_hash
        repo_changed = repo_hash != base_hash

        if source_changed and not repo_changed:
            shutil.copy2(source, repo)
            log.info("[%s] Source updated, copying to repo: %s", self.name, source.name)
            self._sync_state.set_hash(key, source_content)
        elif repo_changed and not source_changed:
            shutil.copy2(repo, source)
            log.info("[%s] Repo updated, copying to source: %s", self.name, repo.name)
            self._sync_state.set_hash(key, repo_content)
        else:
            merge_conflicting_file(local=source, repo=repo)
            merged_content = repo.read_text()
            self._sync_state.set_hash(key, merged_content)

