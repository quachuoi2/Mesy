"""Interface shared by the symlink and copy sync modes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class SyncMode(ABC):

    @abstractmethod
    def sync_file(self, local: Path, repo: Path, label: str) -> None:
        """Reconcile one file between local and repo."""

    @abstractmethod
    def sync_dir(self, local: Path, repo: Path, label: str) -> None:
        """Reconcile a whole folder, subfolders included."""


def list_files(root: Path) -> list[Path]:
    return [f for f in root.rglob("*") if f.is_file()]
