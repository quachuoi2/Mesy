"""Polling detector: fallback when watchdog is not installed, compares the last-modified times of .md files."""

from __future__ import annotations

import time
from pathlib import Path

from src.factory import RepoCtx

POLL_MIN_SECONDS = 60.0


class PollingDetector:
    def start(self, repo_ctxs: list[RepoCtx]) -> None:
        now = time.time()
        self._last_change = {repo_ctx.name: get_md_modified_times(repo_ctx.repo_base) for repo_ctx in repo_ctxs}
        self._last_check = {repo_ctx.name: now for repo_ctx in repo_ctxs}

    def changed(self, repo_ctx: RepoCtx, now: float) -> bool:
        interval = max(float(repo_ctx.settings["debounce_seconds"]), POLL_MIN_SECONDS)
        if now - self._last_check[repo_ctx.name] < interval:
            return False

        self._last_check[repo_ctx.name] = now

        modified_times = get_md_modified_times(repo_ctx.repo_base)
        if modified_times == self._last_change[repo_ctx.name]:
            return False

        self._last_change[repo_ctx.name] = modified_times
        return True

    def stop(self) -> None:
        pass


def get_md_modified_times(repo_base: Path) -> dict[str, float]:
    """Last-modified time of each .md file under repo_base, keyed by path."""
    modified_times = {}
    for md_file in repo_base.rglob("*.md"):
        if ".git" in md_file.parts:
            continue
        try:
            modified_times[str(md_file)] = md_file.stat().st_mtime
        except OSError:
            pass
    return modified_times
