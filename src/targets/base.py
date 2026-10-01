"""Base class for Mesy sync targets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.config import DEFAULT_FILE_PATTERNS
from src.sync_modes.base import SyncMode


class SyncTarget(ABC):
    """Base class for a sync target (e.g. Claude Code, OpenClaw)."""

    name: str
    src: Path          # tool's directory (e.g. ~/.openclaw/workspace)
    target_dir: Path        # subdirectory in the data repo (e.g. AlfredMemory/openclaw/main)
    mode: SyncMode
    repo_base: Path        # root of the data repo this target belongs to
    file_patterns: list[str]
    exclude: list[str]

    def __init__(
        self,
        name: str,
        src: Path,
        target_dir: Path,
        mode: SyncMode,
        repo_base: Path | None = None,
        file_patterns: list[str] | None = None,
        exclude: list[str] | None = None,
    ):
        self.name = name
        self.src = src
        self.target_dir = target_dir
        self.mode = mode
        self.repo_base = repo_base if repo_base is not None else target_dir.parent
        self.file_patterns = file_patterns or list(DEFAULT_FILE_PATTERNS)
        self.exclude = exclude or []

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
