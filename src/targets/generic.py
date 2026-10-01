"""Generic sync target — walks a tool folder file by file, for OpenClaw and future tools."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from src.targets.base import SyncTarget


@dataclass
class FilePair:
    """A single file matched between source and repo (GenericTarget)."""

    src_file: Path
    repo_file: Path


class GenericTarget(SyncTarget):

    def setup(self) -> None:
        """Initial migration — same pipeline as scan()."""
        self.scan()

    def scan(self) -> None:
        """Pair source and repo files, then sync each pair."""
        # A source that is the repo folder itself (DMNotes) only needs git sync.
        if not self.src.exists() or self.src.resolve() == self.target_dir.resolve():
            return

        self.target_dir.mkdir(parents=True, exist_ok=True)
        pairs = self._walk_and_adopt()
        self._sync(pairs)

    def get_stage_dirs(self) -> list[str]:
        """Return the repo_subdir relative to repo_base for this target."""
        try:
            rel = self.target_dir.relative_to(self.repo_base)
            return [str(rel) + "/"]

        except ValueError:
            return [str(self.target_dir) + "/"]

    def _walk_and_adopt(self) -> list[FilePair]:
        """Walk source and repo dirs recursively; return (source, repo) pairs.

        File links are paired like real files; the sync mode decides what to do with them.
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
            if is_excluded(item.name, self.exclude):
                continue

            src_names.add(item.name)

            if item.is_symlink() and item.is_dir():
                continue

            if item.is_dir():
                pairs.extend(self._walk_dir(item, target_dir / item.name))
                continue

            if not matches_patterns(item.name, self.file_patterns):
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

            if not matches_patterns(repo_item.name, self.file_patterns):
                continue

            pairs.append(FilePair(src_file=src_dir / repo_item.name, repo_file=repo_item))

        return pairs

    def _sync(self, pairs: list[FilePair]) -> None:
        for pair in pairs:
            self.mode.sync_file(pair.src_file, pair.repo_file, label=f"{self.name} {pair.src_file.name}")


def is_excluded(name: str, exclude: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat.rstrip("/")) for pat in exclude)


def matches_patterns(name: str, file_patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in file_patterns)
