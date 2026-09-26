"""Watchdog loop: syncs a data repo after its .md files change."""

from __future__ import annotations

import logging
import time

from src.cycle import commit_repo, run_intervals, scan_repo
from src.factory import RepoContext

try:
    from watchdog.observers import Observer
except ImportError:
    Observer = None

log = logging.getLogger("mesy")


def run_with_watchdog(repo_contexts: list[RepoContext]) -> None:
    """Use watchdog for file-change detection across all repos."""
    from watchdog.events import FileSystemEventHandler

    last_change: dict[str, float] = {ctx.name: 0.0 for ctx in repo_contexts}

    class MdChangeHandler(FileSystemEventHandler):
        def __init__(self, repo_name: str) -> None:
            self._repo_name = repo_name

        def on_any_event(self, event):
            if event.is_directory:
                return
            if event.src_path.endswith(".md") and ".git" not in event.src_path:
                last_change[self._repo_name] = time.time()

    observer = Observer()
    for ctx in repo_contexts:
        observer.schedule(MdChangeHandler(ctx.name), str(ctx.data_dir), recursive=True)
        log.info(
            "Watching [%s] with watchdog (debounce=%ss, data_dir=%s)",
            ctx.name,
            ctx.settings["debounce_seconds"],
            ctx.data_dir,
        )

    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    last_scan = {ctx.name: time.time() for ctx in repo_contexts}

    observer.start()
    try:
        while True:
            time.sleep(1)
            now = time.time()
            for ctx in repo_contexts:
                _commit_if_settled(ctx, now, last_change)
                run_intervals(ctx, now, last_pull, last_scan)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def _commit_if_settled(ctx: RepoContext, now: float, last_change: dict[str, float]) -> None:
    if last_change[ctx.name] and (now - last_change[ctx.name]) > float(ctx.settings["debounce_seconds"]):
        last_change[ctx.name] = 0.0
        if scan_repo(ctx):
            commit_repo(ctx)
