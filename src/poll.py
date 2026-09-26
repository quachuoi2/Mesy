"""Polling loop: fallback when watchdog is not installed."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from src.cycle import commit_repo, run_intervals, scan_repo
from src.factory import RepoContext

log = logging.getLogger("mesy")


def run_with_polling(repo_contexts: list[RepoContext]) -> None:
    """Fallback: poll for changes using mtime comparison, across all repos."""
    # Shortest debounce across repos, so no repo's debounce window is silently skipped.
    poll_interval = min(max(float(ctx.settings["debounce_seconds"]), 60) for ctx in repo_contexts)
    log.info("Polling mode (interval=%ss)", poll_interval)

    prev_mtimes = {ctx.name: get_md_mtimes(ctx.data_dir) for ctx in repo_contexts}
    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    last_scan = {ctx.name: time.time() for ctx in repo_contexts}

    try:
        while True:
            time.sleep(poll_interval)
            now = time.time()
            for ctx in repo_contexts:
                current = get_md_mtimes(ctx.data_dir)
                if current != prev_mtimes[ctx.name]:
                    prev_mtimes[ctx.name] = current
                    if scan_repo(ctx):
                        commit_repo(ctx)
                run_intervals(ctx, now, last_pull, last_scan)
    except KeyboardInterrupt:
        pass


def get_md_mtimes(data_dir: Path) -> dict[str, float]:
    """Get modification times of all .md files in data_dir."""
    mtimes = {}
    for md_file in data_dir.rglob("*.md"):
        if ".git" in md_file.parts:
            continue
        try:
            mtimes[str(md_file)] = md_file.stat().st_mtime
        except OSError:
            pass
    return mtimes
