"""Generic sync target — flat symlink for OpenClaw and future tools."""

import filecmp
import fnmatch
import logging
import shutil
from pathlib import Path

from src.config import DATA_DIR
from src.git_utils import merge_conflicting_file
from src.sync_state import get_sync_state
from src.targets.base import SyncTarget

log = logging.getLogger("mesy")


class GenericTarget(SyncTarget):

    def __init__(
        self,
        name: str,
        source: Path,
        repo_dir: Path,
        file_patterns: list[str] | None = None,
        exclude: list[str] | None = None,
        sync_mode: str = "symlink",
    ):
        super().__init__(
            name=name,
            source=source,
            repo_dir=repo_dir,
            file_patterns=file_patterns,
            exclude=exclude,
            sync_mode=sync_mode,
        )

    def setup(self) -> None:
        """Initial migration: copy files from source into repo, replace with symlinks or copies."""
        if not self.source.exists():
            log.info("[%s] Source %s does not exist, skipping setup", self.name, self.source)
            return

        self.repo_dir.mkdir(parents=True, exist_ok=True)
        if self.sync_mode == "copy":
            self._adopt_directory_copy(self.source, self.repo_dir)
        else:
            self._adopt_directory(self.source, self.repo_dir)

    def scan(self) -> None:
        """Detect new files in source, adopt them via the configured sync mode."""
        if not self.source.exists():
            return

        self.repo_dir.mkdir(parents=True, exist_ok=True)
        if self.sync_mode == "copy":
            self._adopt_directory_copy(self.source, self.repo_dir)
        else:
            self._adopt_directory(self.source, self.repo_dir)

    def get_stage_dirs(self) -> list[str]:
        # Return the repo_subdir relative to DATA_DIR
        from src.config import DATA_DIR
        try:
            rel = self.repo_dir.relative_to(DATA_DIR)
            return [str(rel) + "/"]
        except ValueError:
            return [str(self.repo_dir) + "/"]

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

    def _adopt_directory(self, source_dir: Path, repo_dir: Path) -> None:
        """Adopt files from source_dir into repo_dir, replacing originals with symlinks."""
        repo_dir.mkdir(parents=True, exist_ok=True)

        for item in sorted(source_dir.iterdir()):
            if self._should_exclude(item.name):
                continue

            if item.is_symlink():
                # Already a symlink — verify it points into our repo
                continue

            if item.is_dir():
                # Recurse into subdirectories (e.g. memory/)
                self._adopt_directory(item, repo_dir / item.name)
                continue

            if not self._matches_patterns(item.name):
                continue

            dest = repo_dir / item.name
            if not dest.exists():
                shutil.copy2(item, dest)
                log.info("[%s] Copied %s into repo", self.name, item.name)
            elif not filecmp.cmp(item, dest, shallow=False):
                merge_conflicting_file(local=item, repo=dest)

            # Replace original with symlink
            item.unlink()
            item.symlink_to(dest)
            log.info("[%s] Symlinked %s", self.name, item.name)

        # Also create symlinks for repo files that don't exist in source
        # (pulled from another device)
        for repo_file in sorted(repo_dir.iterdir()):
            if repo_file.is_dir():
                # Ensure source subdir exists and recurse
                source_subdir = source_dir / repo_file.name
                if not source_subdir.exists():
                    source_subdir.mkdir(parents=True, exist_ok=True)
                if not source_subdir.is_symlink():
                    self._adopt_directory(source_subdir, repo_file)
                continue

            if not self._matches_patterns(repo_file.name):
                continue

            source_file = source_dir / repo_file.name
            if source_file.is_symlink():
                if source_file.resolve() == repo_file.resolve():
                    # Already correct — nothing to do
                    continue
                # Broken or wrong symlink — replace it
                source_file.unlink()
            elif source_file.exists():
                # Regular file — leave it for the adoption loop above to handle
                continue
            source_file.symlink_to(repo_file)
            log.info("[%s] Symlinked %s (from repo)", self.name, repo_file.name)

    def _adopt_directory_copy(self, source_dir: Path, repo_dir: Path) -> None:
        """Bidirectional copy sync: keep source and repo in sync without symlinks.

        - Files only in source → copy to repo
        - Files only in repo → copy to source
        - Files in both with different content → delegate to merge_conflicting_file
        - Symlinks in source (leftovers from prior symlink mode) → migrate to real copies
        """
        repo_dir.mkdir(parents=True, exist_ok=True)

        # Pass 1: source → repo
        for item in sorted(source_dir.iterdir()):
            if self._should_exclude(item.name):
                continue

            if item.is_dir():
                self._adopt_directory_copy(item, repo_dir / item.name)
                continue

            if not self._matches_patterns(item.name):
                continue

            dest = repo_dir / item.name

            if item.is_symlink():
                # Leftover symlink from previous symlink mode — migrate to real copy.
                real_content = item.resolve()
                item.unlink()
                if real_content.exists():
                    shutil.copy2(real_content, item)
                    log.info("[%s] Migrated symlink -> real file: %s", self.name, item.name)
                elif dest.exists():
                    shutil.copy2(dest, item)
                    log.info(
                        "[%s] Symlink target missing, restored from repo: %s",
                        self.name,
                        item.name,
                    )
                else:
                    log.warning(
                        "[%s] Symlink %s has no resolvable target, skipping",
                        self.name,
                        item.name,
                    )
                    continue

            if not dest.exists():
                shutil.copy2(item, dest)
                log.info("[%s] Copied %s -> repo (copy mode)", self.name, item.name)
                sync_state = get_sync_state()
                key = self._repo_key(dest)
                sync_state.set_hash(key, item.read_text())
            elif not filecmp.cmp(item, dest, shallow=False):
                self._three_way_sync(item, dest)

        # Pass 2: repo → source (files that exist only in repo, e.g. from another device)
        for repo_file in sorted(repo_dir.iterdir()):
            if repo_file.is_dir():
                source_subdir = source_dir / repo_file.name
                source_subdir.mkdir(parents=True, exist_ok=True)
                self._adopt_directory_copy(source_subdir, repo_file)
                continue

            if not self._matches_patterns(repo_file.name):
                continue

            source_file = source_dir / repo_file.name

            if source_file.is_symlink():
                # Another leftover symlink — replace with real copy from repo
                source_file.unlink()
                shutil.copy2(repo_file, source_file)
                log.info(
                    "[%s] Replaced leftover symlink with copy from repo: %s",
                    self.name,
                    repo_file.name,
                )
            elif not source_file.exists():
                shutil.copy2(repo_file, source_file)
                log.info("[%s] Copied %s -> source (copy mode)", self.name, repo_file.name)
                sync_state = get_sync_state()
                key = self._repo_key(repo_file)
                sync_state.set_hash(key, repo_file.read_text())
            # If source_file exists as a regular file it was already handled in Pass 1

    def _repo_key(self, repo_file: Path) -> str:
        """Return the repo-relative path as a stable key for hash storage."""
        from src.config import DATA_DIR
        try:
            return str(repo_file.relative_to(DATA_DIR))
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
        sync_state = get_sync_state()
        key = self._repo_key(repo)

        source_content = source.read_text()
        repo_content = repo.read_text()

        source_hash = sync_state.compute_hash(source_content)
        repo_hash = sync_state.compute_hash(repo_content)
        base_hash = sync_state.get_hash(key)

        if source_hash == repo_hash:
            # Files are identical — just make sure hash is recorded.
            if base_hash != source_hash:
                sync_state.set_hash(key, source_content)
            return

        if base_hash is None:
            # First sync: no history — source wins.
            shutil.copy2(source, repo)
            log.info("[%s] First sync, source wins: %s", self.name, source.name)
            sync_state.set_hash(key, source_content)
            return

        source_changed = source_hash != base_hash
        repo_changed = repo_hash != base_hash

        if source_changed and not repo_changed:
            shutil.copy2(source, repo)
            log.info("[%s] Source updated, copying to repo: %s", self.name, source.name)
            sync_state.set_hash(key, source_content)
        elif repo_changed and not source_changed:
            shutil.copy2(repo, source)
            log.info("[%s] Repo updated, copying to source: %s", self.name, repo.name)
            sync_state.set_hash(key, repo_content)
        else:
            # Both sides changed — real conflict.
            merge_conflicting_file(local=source, repo=repo)
            merged_content = repo.read_text()
            sync_state.set_hash(key, merged_content)
