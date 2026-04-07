"""Base class for Mesy sync targets."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

log = logging.getLogger("mesy")


class SyncTarget(ABC):
    """Base class for a sync target (e.g. Claude Code, OpenClaw)."""

    name: str
    source: Path          # tool's directory (e.g. ~/.openclaw/workspace)
    repo_dir: Path        # subdirectory in Mesy repo (e.g. Mesy/openclaw/main)
    file_patterns: list[str]
    exclude: list[str]

    def __init__(
        self,
        name: str,
        source: Path,
        repo_dir: Path,
        file_patterns: list[str] | None = None,
        exclude: list[str] | None = None,
        sync_mode: str = "symlink",
    ):
        self.name = name
        self.source = source
        self.repo_dir = repo_dir
        self.file_patterns = file_patterns or ["*.md"]
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

    def get_watched_paths(self) -> list[Path]:
        """Return paths that should be watched for changes."""
        return [self.source, self.repo_dir]

    @abstractmethod
    def get_stage_dirs(self) -> list[str]:
        """Return git-add paths relative to REPO_DIR for this target."""
        ...
