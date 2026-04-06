"""Generic sync target — flat symlink for OpenClaw and future tools."""

import filecmp
import fnmatch
import logging
import shutil
from pathlib import Path

from src.git_utils import merge_conflicting_file
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
    ):
        super().__init__(
            name=name,
            source=source,
            repo_dir=repo_dir,
            file_patterns=file_patterns,
            exclude=exclude,
        )

    def setup(self) -> None:
        """Initial migration: copy files from source into repo, replace with symlinks."""
        if not self.source.exists():
            log.info("[%s] Source %s does not exist, skipping setup", self.name, self.source)
            return

        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self._adopt_directory(self.source, self.repo_dir)

    def scan(self) -> None:
        """Detect new files in source that aren't symlinked yet, adopt them."""
        if not self.source.exists():
            return

        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self._adopt_directory(self.source, self.repo_dir)

    def get_stage_dirs(self) -> list[str]:
        # Return the repo_subdir relative to REPO_DIR
        from src.config import REPO_DIR
        try:
            rel = self.repo_dir.relative_to(REPO_DIR)
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
            if not source_file.exists():
                source_file.symlink_to(repo_file)
                log.info("[%s] Symlinked %s (from repo)", self.name, repo_file.name)
