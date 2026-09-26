"""Codex sync target: global AGENTS.md and the native memories folder."""

from __future__ import annotations

from src.targets.base import SyncTarget


class CodexTarget(SyncTarget):

    def setup(self) -> None:
        self.scan()

    def scan(self) -> None:
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self._sync_file(self.source / "AGENTS.md", self.repo_dir / "AGENTS.md", label="Codex AGENTS.md")
        self._sync_dir(self.source / "memories", self.repo_dir / "memories", label="Codex memories")

    def get_stage_dirs(self) -> list[str]:
        return [self.repo_dir.relative_to(self.data_dir).as_posix() + "/"]
