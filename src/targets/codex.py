"""Codex sync target: global AGENTS.md and the native memories folder."""

from __future__ import annotations

from src.targets.base import SyncTarget


class CodexTarget(SyncTarget):

    def setup(self) -> None:
        self.scan()

    def scan(self) -> None:
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self._sync_file(self.src / "AGENTS.md", self.target_dir / "AGENTS.md", label="Codex AGENTS.md")
        self._sync_dir(self.src / "memories", self.target_dir / "memories", label="Codex memories")

    def get_stage_dirs(self) -> list[str]:
        return [self.target_dir.relative_to(self.repo_base).as_posix() + "/"]
