"""Interface shared by the legacy and isolated git sync."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class GitSync(ABC):
    """Moves one data repo's commits to and from its remote."""

    isolated = False

    @abstractmethod
    def push(self, data_dir: Path, stage_dirs: list[str]) -> bool:
        """Commit stage_dirs and publish. True when remote changes reached the live files."""

    @abstractmethod
    def pull(self, data_dir: Path) -> None:
        """Bring in remote changes without committing."""
