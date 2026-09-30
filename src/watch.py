"""Watchdog loop: syncs a data repo after its .md files change."""

from __future__ import annotations

import logging
import time

from src.cycle import commit_repo, run_intervals, scan_repo
from src.factory import RepoCtx

try:
    from watchdog.observers import Observer
except ImportError:
    Observer = None

log = logging.getLogger("mesy")


def run_with_watchdog(repo_ctxs: list[RepoCtx]) -> None:
    """Use watchdog for file-change detection across all repos."""
    from watchdog.events import FileSystemEventHandler

    last_change: dict[str, float] = {repo_ctx.name: 0.0 for repo_ctx in repo_ctxs}

    class MdChangeHandler(FileSystemEventHandler):
        def __init__(self, repo_name: str) -> None:
            self._repo_name = repo_name

        def on_any_event(self, event):
            if event.is_directory:
                return
            # A save that writes a temp file and renames it reports the .md name in dest_path.
            paths = (event.src_path, getattr(event, "dest_path", ""))
            if any(path.endswith(".md") and ".git" not in path for path in paths):
                last_change[self._repo_name] = time.time()

    observer = Observer()
    for repo_ctx in repo_ctxs:
        observer.schedule(MdChangeHandler(repo_ctx.name), str(repo_ctx.repo_base), recursive=True)
        log.info(
            "Watching [%s] with watchdog (debounce=%ss, repo_base=%s)",
            repo_ctx.name,
            repo_ctx.settings["debounce_seconds"],
            repo_ctx.repo_base,
        )

    last_pull: dict[str, float] = {repo_ctx.name: time.time() for repo_ctx in repo_ctxs}
    last_scan = {repo_ctx.name: time.time() for repo_ctx in repo_ctxs}

    observer.start()
    try:
        while True:
            time.sleep(1)
            now = time.time()
            for repo_ctx in repo_ctxs:
                _commit_if_settled(repo_ctx, now, last_change)
                run_intervals(repo_ctx, now, last_pull, last_scan)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def _commit_if_settled(repo_ctx: RepoCtx, now: float, last_change: dict[str, float]) -> None:
    if last_change[repo_ctx.name] and (now - last_change[repo_ctx.name]) > float(repo_ctx.settings["debounce_seconds"]):
        last_change[repo_ctx.name] = 0.0
        if scan_repo(repo_ctx):
            commit_repo(repo_ctx)
