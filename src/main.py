"""Mesy main loop — multi-repo, multi-target sync daemon."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from src.config import (
    REPO_DIR,
    RepoContext,
    get_mesy_settings,
    load_config,
    load_repos,
    load_targets,
)
from src.git_utils import commit_and_push, pull_remote
from src.targets.base import SyncTarget
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.generic import GenericTarget

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("mesy")


def build_targets(target_defs: list[dict], data_dir: Path) -> list[SyncTarget]:
    """Instantiate SyncTarget objects from config definitions.

    Args:
        target_defs: parsed target definitions from load_targets().
        data_dir: the data repo root — passed to each target so it can
            compute stage dirs and hash file paths without touching globals.
    """
    targets = []
    for tdef in target_defs:
        repo_dir = data_dir / tdef["repo_subdir"]
        sync_mode = tdef.get("sync_mode", "symlink")
        if tdef["type"] == "claude-code":
            targets.append(ClaudeCodeTarget(
                source=tdef["source"],
                repo_dir=repo_dir,
                data_dir=data_dir,
                sync_mode=sync_mode,
            ))
        elif tdef["type"] == "generic":
            targets.append(GenericTarget(
                name=tdef["name"],
                source=tdef["source"],
                repo_dir=repo_dir,
                data_dir=data_dir,
                file_patterns=tdef.get("file_patterns", ["*.md"]),
                exclude=tdef.get("exclude", []),
                sync_mode=sync_mode,
            ))
        else:
            log.warning("Unknown target type: %s", tdef["type"])
    return targets


def get_all_stage_dirs(targets: list[SyncTarget]) -> list[str]:
    """Collect unique git-add directories from all targets."""
    dirs = []
    seen = set()
    for t in targets:
        for d in t.get_stage_dirs():
            if d not in seen:
                seen.add(d)
                dirs.append(d)
    return dirs


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


def _build_repo_contexts(repo_list: list[dict]) -> list[RepoContext]:
    """Build RepoContext objects from a list of repo dicts (from load_repos())."""
    contexts = []
    for repo_info in repo_list:
        data_dir = repo_info["path"]
        cfg = load_config(data_dir=data_dir)
        settings = get_mesy_settings(cfg)
        target_defs = load_targets(cfg)
        targets = build_targets(target_defs, data_dir=data_dir)
        stage_dirs = get_all_stage_dirs(targets)
        contexts.append(RepoContext(
            name=repo_info["name"],
            data_dir=data_dir,
            targets=targets,
            stage_dirs=stage_dirs,
            settings=settings,
        ))
    return contexts


def run_with_watchdog(repo_contexts: list[RepoContext], settings: dict) -> None:
    """Use watchdog for file-change detection across all repos."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    scan_interval = float(settings["scan_interval_seconds"])

    # Per-repo last-change timestamp, keyed by repo name.
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
        ctx_debounce = float(ctx.settings.get("debounce_seconds", settings["debounce_seconds"]))
        handler = MdChangeHandler(repo_name=ctx.name)
        observer.schedule(handler, str(ctx.data_dir), recursive=True)
        log.info(
            "Watching [%s] with watchdog (debounce=%ss, data_dir=%s)",
            ctx.name,
            ctx_debounce,
            ctx.data_dir,
        )

    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    last_scan = time.time()

    observer.start()
    try:
        while True:
            time.sleep(1)
            now = time.time()

            for ctx in repo_contexts:
                ctx_debounce = float(ctx.settings.get("debounce_seconds", settings["debounce_seconds"]))
                ctx_pull_interval = float(ctx.settings.get("pull_interval_seconds", settings["pull_interval_seconds"]))

                if last_change[ctx.name] and (now - last_change[ctx.name]) > ctx_debounce:
                    last_change[ctx.name] = 0.0
                    commit_and_push(ctx.stage_dirs, data_dir=ctx.data_dir)

                if (now - last_pull[ctx.name]) > ctx_pull_interval:
                    last_pull[ctx.name] = now
                    pull_remote(data_dir=ctx.data_dir)

            if (now - last_scan) > scan_interval:
                last_scan = now
                for ctx in repo_contexts:
                    for target in ctx.targets:
                        target.scan()

    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def run_with_polling(repo_contexts: list[RepoContext], settings: dict) -> None:
    """Fallback: poll for changes using mtime comparison, across all repos."""
    scan_interval = float(settings["scan_interval_seconds"])

    # Use the minimum debounce across repos as the tick interval so no repo's
    # debounce window is silently skipped.
    min_debounce = min(
        max(float(ctx.settings.get("debounce_seconds", settings["debounce_seconds"])), 60)
        for ctx in repo_contexts
    ) if repo_contexts else max(float(settings["debounce_seconds"]), 60)
    poll_interval = min_debounce

    log.info("Polling mode (interval=%ss)", poll_interval)

    prev_mtimes: dict[str, dict[str, float]] = {
        ctx.name: get_md_mtimes(ctx.data_dir) for ctx in repo_contexts
    }
    last_pull: dict[str, float] = {ctx.name: time.time() for ctx in repo_contexts}
    last_scan = time.time()

    try:
        while True:
            time.sleep(poll_interval)
            now = time.time()

            for ctx in repo_contexts:
                ctx_pull_interval = float(ctx.settings.get("pull_interval_seconds", settings["pull_interval_seconds"]))

                current_mtimes = get_md_mtimes(ctx.data_dir)
                if current_mtimes != prev_mtimes[ctx.name]:
                    prev_mtimes[ctx.name] = current_mtimes
                    commit_and_push(ctx.stage_dirs, data_dir=ctx.data_dir)

                if (now - last_pull[ctx.name]) > ctx_pull_interval:
                    last_pull[ctx.name] = now
                    pull_remote(data_dir=ctx.data_dir)

            if (now - last_scan) > scan_interval:
                last_scan = now
                for ctx in repo_contexts:
                    for target in ctx.targets:
                        target.scan()

    except KeyboardInterrupt:
        pass


def main() -> None:
    repo_list = load_repos()
    if not repo_list:
        log.error(
            "repos.conf not found or empty at %s — Mesy now requires a repos.conf "
            "with at least one [repo:*] section.",
            REPO_DIR / "repos.conf",
        )
        return

    log.info("Loaded %d repo(s) from repos.conf", len(repo_list))
    repo_contexts = _build_repo_contexts(repo_list)

    if not any(ctx.targets for ctx in repo_contexts):
        log.error("No targets configured in any sync.conf. Add [target:*] sections.")
        return

    #refactor this. Each repo will have its own settings. Global default is built into load_config()
    # Global settings used as fallback defaults (e.g. scan_interval which is top-level).
    first_data_dir = repo_contexts[0].data_dir
    cfg = load_config(data_dir=first_data_dir)
    settings = get_mesy_settings(cfg)

    log.info("Mesy sync daemon starting (repo: %s)", REPO_DIR)
    for ctx in repo_contexts:
        log.info(
            "  [%s] data_dir=%s, targets=%s",
            ctx.name,
            ctx.data_dir,
            ", ".join(t.name for t in ctx.targets),
        )

    # Initial sync across all repos.
    for ctx in repo_contexts:
        pull_remote(data_dir=ctx.data_dir)
        for target in ctx.targets:
            target.scan()
        commit_and_push(ctx.stage_dirs, data_dir=ctx.data_dir)

    # Choose watch method.
    try:
        import watchdog  # noqa: F401
        run_with_watchdog(repo_contexts, settings)
    except ImportError:
        log.info("watchdog not installed, falling back to polling")
        run_with_polling(repo_contexts, settings)
