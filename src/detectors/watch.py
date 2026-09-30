"""Watchdog detector: file events, reported once a repo has been quiet for its debounce."""

from __future__ import annotations

import logging
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from src.factory import RepoCtx

log = logging.getLogger("mesy")


class WatchdogDetector:
    def __init__(self) -> None:
        self._observer = Observer()
        self._last_change: dict[str, float] = {}

    def start(self, repo_ctxs: list[RepoCtx]) -> None:
        for repo_ctx in repo_ctxs:
            self._last_change[repo_ctx.name] = 0.0
            self._observer.schedule(
                _MdChangeHandler(repo_ctx.name, self._last_change), str(repo_ctx.repo_base), recursive=True
            )
            log.info(
                "Watching [%s] with watchdog (debounce=%ss, repo_base=%s)",
                repo_ctx.name,
                repo_ctx.settings["debounce_seconds"],
                repo_ctx.repo_base,
            )

        self._observer.start()

    def changed(self, repo_ctx: RepoCtx, now: float) -> bool:
        last = self._last_change[repo_ctx.name]
        if not last or now - last <= float(repo_ctx.settings["debounce_seconds"]):
            return False

        self._last_change[repo_ctx.name] = 0.0
        return True

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()


class _MdChangeHandler(FileSystemEventHandler):
    def __init__(self, repo_name: str, last_change: dict[str, float]) -> None:
        self._repo_name = repo_name
        self._last_change = last_change

    def on_any_event(self, event):
        if event.is_directory:
            return

        # A save that writes a temp file and renames it reports the .md name in dest_path.
        paths = (event.src_path, getattr(event, "dest_path", ""))
        if any(path.endswith(".md") and ".git" not in path for path in paths):
            self._last_change[self._repo_name] = time.time()
