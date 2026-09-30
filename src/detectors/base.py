"""Change detection seam: tells the main loop when a data repo's .md files changed."""

from __future__ import annotations

from typing import Protocol

from src.factory import RepoCtx


class ChangeDetector(Protocol):
    def start(self, repo_ctxs: list[RepoCtx]) -> None: ...

    def changed(self, repo_ctx: RepoCtx, now: float) -> bool: ...

    def stop(self) -> None: ...
