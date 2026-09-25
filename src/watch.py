"""Watch loops that decide when each data repo syncs."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from src.cycle import commit_repo, pull_repo, scan_repo
from src.factory import RepoContext

try:
    from watchdog.observers import Observer
except ImportError:
    Observer = None

log = logging.getLogger("mesy")


def run_with_watchdog(repo_contexts: list[RepoContext]) -> None:
    """Use watchdog for file-change detection across all repos."""
    from watchdog.events import FileSystemEventHandler
    if Observer is None:
        raise ImportError("watchdog is not installed")

    last_change: dict[str, float] = {ctx.name: 0.0 for ctx in repo_contexts}
    observer = Observer()
    for ctx in repo_contexts:
        _observe(observer, ctx, _change_handler(FileSystemEventHandler, ctx, last_change))

    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    last_scan = {ctx.name: time.time() for ctx in repo_contexts}

    observer.start()
    try:
        while True:
            time.sleep(1)
            now = time.time()
            for ctx in repo_contexts:
                _commit_if_settled(ctx, now, last_change)
                _run_intervals(ctx, now, last_pull, last_scan)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def _change_handler(handler_base: type, ctx: RepoContext, last_change: dict[str, float]):
    class TargetChangeHandler(handler_base):
        def on_any_event(self, event):
            if event.event_type not in {"created", "modified", "moved", "deleted"}:
                return
            if any(_event_relevant(ctx, p) for p in (event.src_path, getattr(event, "dest_path", "")) if p):
                last_change[ctx.name] = time.time()

    return TargetChangeHandler()


def _observe(observer, ctx: RepoContext, handler) -> None:
    debounce = float(ctx.settings["debounce_seconds"])
    observer.schedule(handler, str(ctx.data_dir), recursive=True)
    extra_paths = {p for t in ctx.targets for p in t.get_watched_paths() if p.exists()}
    for path in extra_paths:
        if path != ctx.data_dir and ctx.data_dir not in path.parents:
            observer.schedule(handler, str(path), recursive=True)
    log.info(
        "Watching [%s] with watchdog (debounce=%ss, data_dir=%s)",
        ctx.name,
        debounce,
        ctx.data_dir,
    )


def _commit_if_settled(ctx: RepoContext, now: float, last_change: dict[str, float]) -> None:
    if last_change[ctx.name] and (now - last_change[ctx.name]) > float(ctx.settings["debounce_seconds"]):
        last_change[ctx.name] = 0.0
        if scan_repo(ctx):
            commit_repo(ctx)


def run_with_polling(repo_contexts: list[RepoContext]) -> None:
    """Fallback: poll for changes using mtime comparison, across all repos."""
    poll_interval = _poll_interval(repo_contexts)
    log.info("Polling mode (interval=%ss)", poll_interval)

    snapshots: dict[str, dict] = {ctx.name: _snapshot(ctx) for ctx in repo_contexts}
    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    initial_scan = time.time()
    last_scan = {ctx.name: initial_scan for ctx in repo_contexts}
    pending = {ctx.name: None for ctx in repo_contexts}

    try:
        while True:
            time.sleep(poll_interval)
            now = time.time()
            for ctx in repo_contexts:
                _check_snapshot(ctx, now, snapshots, pending)
                _flush_pending(ctx, now, snapshots, pending)
                _run_intervals(ctx, now, last_pull, last_scan)
    except KeyboardInterrupt:
        pass


def _poll_interval(repo_contexts: list[RepoContext]) -> float:
    # The shortest timing across repos, so no repo's debounce window is silently skipped.
    return min(
        max(min(float(ctx.settings[key]) for key in
                ("debounce_seconds", "scan_interval_seconds", "pull_interval_seconds")), 1)
        for ctx in repo_contexts
    )


def _check_snapshot(ctx: RepoContext, now: float, snapshots: dict, pending: dict) -> None:
    current = _snapshot(ctx)
    if current == snapshots[ctx.name]:
        return
    snapshots[ctx.name] = current
    if ctx.git_sync.isolated:
        pending[ctx.name] = now
    elif scan_repo(ctx):
        _commit_and_resnapshot(ctx, snapshots)


def _flush_pending(ctx: RepoContext, now: float, snapshots: dict, pending: dict) -> None:
    changed_at = pending[ctx.name]
    if changed_at is not None and now - changed_at >= float(ctx.settings["debounce_seconds"]):
        pending[ctx.name] = None
        if scan_repo(ctx):
            _commit_and_resnapshot(ctx, snapshots)


def _commit_and_resnapshot(ctx: RepoContext, snapshots: dict) -> None:
    commit_repo(ctx)
    snapshots[ctx.name] = _snapshot(ctx)


def _run_intervals(ctx: RepoContext, now: float, last_pull: dict[str, float], last_scan: dict[str, float]) -> None:
    if (now - last_pull[ctx.name]) > float(ctx.settings["pull_interval_seconds"]):
        last_pull[ctx.name] = now
        pull_repo(ctx)
    if now - last_scan[ctx.name] > float(ctx.settings["scan_interval_seconds"]):
        last_scan[ctx.name] = now
        scan_repo(ctx)


def _watch_roots(ctx: RepoContext) -> list[Path]:
    paths = [ctx.data_dir / p for p in ctx.stage_dirs]
    for target in ctx.targets:
        paths.extend(target.get_source_paths())
    return paths


def _event_relevant(ctx: RepoContext, path: str) -> bool:
    candidate = Path(os.path.abspath(path))
    if any(p in {".git", ".mesy-local"} or p.startswith(".mesy-") for p in candidate.parts):
        return False
    paths = _watch_roots(ctx)
    if not paths:
        return candidate.suffix == ".md"
    return any(candidate == p or p in candidate.parents for p in paths)


def _snapshot(ctx: RepoContext) -> dict:
    # Keep the legacy Markdown scan for repositories without configured targets.
    if not ctx.targets:
        return get_md_mtimes(ctx.data_dir)
    result = {}
    for root in _watch_roots(ctx):
        entries = [root]
        if root.is_dir() and not root.is_symlink():
            entries.extend(root.rglob("*"))
        for path in entries:
            if _event_relevant(ctx, str(path)):
                _record(path, result)
    return result


def _record(path: Path, result: dict) -> None:
    try:
        stat = path.lstat()
        if path.is_symlink():
            result[str(path)] = ("link", os.readlink(path))
        elif path.is_file():
            result[str(path)] = (stat.st_mtime_ns, stat.st_size, stat.st_mode)
    except OSError:
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
