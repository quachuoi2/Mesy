"""Polling loop: fallback when watchdog is not installed."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from src.cycle import commit_repo, run_intervals, scan_repo
from src.factory import RepoCtx

log = logging.getLogger("mesy")


def run_with_polling(repo_ctxs: list[RepoCtx]) -> None:
    """Fallback: poll for changes using mtime comparison, across all repos."""
    # Shortest debounce across repos, so no repo's debounce window is silently skipped.
    poll_interval = min(max(float(repo_ctx.settings["debounce_seconds"]), 60) for repo_ctx in repo_ctxs)
    log.info("Polling mode (interval=%ss)", poll_interval)

    prev_mtimes = {repo_ctx.name: get_md_mtimes(repo_ctx.repo_base) for repo_ctx in repo_ctxs}
    last_pull: dict[str, float] = {repo_ctx.name: time.time() for repo_ctx in repo_ctxs}
    last_scan = {repo_ctx.name: time.time() for repo_ctx in repo_ctxs}

    try:
        while True:
            time.sleep(poll_interval)
            now = time.time()
            for repo_ctx in repo_ctxs:
                current = get_md_mtimes(repo_ctx.repo_base)
                if current != prev_mtimes[repo_ctx.name]:
                    prev_mtimes[repo_ctx.name] = current
                    if scan_repo(repo_ctx):
                        commit_repo(repo_ctx)
                run_intervals(repo_ctx, now, last_pull, last_scan)
    except KeyboardInterrupt:
        pass


def get_md_mtimes(repo_base: Path) -> dict[str, float]:
    """Get modification times of all .md files in repo_base."""
    mtimes = {}
    for md_file in repo_base.rglob("*.md"):
        if ".git" in md_file.parts:
            continue
        try:
            mtimes[str(md_file)] = md_file.stat().st_mtime
        except OSError:
            pass
    return mtimes
