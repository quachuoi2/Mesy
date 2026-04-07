"""Mesy main loop — multi-target sync daemon."""

import logging
import time
from pathlib import Path

from src.config import DATA_DIR, REPO_DIR, get_mesy_settings, load_config, load_targets
from src.git_utils import commit_and_push, pull_remote
from src.targets.base import SyncTarget
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.generic import GenericTarget

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("mesy")


def build_targets(target_defs: list[dict]) -> list[SyncTarget]:
    """Instantiate SyncTarget objects from config definitions."""
    targets = []
    for tdef in target_defs:
        repo_dir = DATA_DIR / tdef["repo_subdir"]
        sync_mode = tdef.get("sync_mode", "symlink")
        if tdef["type"] == "claude-code":
            targets.append(ClaudeCodeTarget(
                source=tdef["source"],
                repo_dir=repo_dir,
                sync_mode=sync_mode,
            ))
        elif tdef["type"] == "generic":
            targets.append(GenericTarget(
                name=tdef["name"],
                source=tdef["source"],
                repo_dir=repo_dir,
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


def get_md_mtimes() -> dict[str, float]:
    """Get modification times of all .md files in data dir."""
    mtimes = {}
    for md_file in DATA_DIR.rglob("*.md"):
        if ".git" in md_file.parts:
            continue
        try:
            mtimes[str(md_file)] = md_file.stat().st_mtime
        except OSError:
            pass
    return mtimes


def run_with_watchdog(targets: list[SyncTarget], settings: dict) -> None:
    """Use watchdog for file-change detection."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    debounce = float(settings["debounce_seconds"])
    pull_interval = float(settings["pull_interval_seconds"])
    scan_interval = float(settings["scan_interval_seconds"])
    stage_dirs = get_all_stage_dirs(targets)

    class MdChangeHandler(FileSystemEventHandler):
        def __init__(self):
            self.last_change = 0.0

        def on_any_event(self, event):
            if event.is_directory:
                return
            if event.src_path.endswith(".md") and ".git" not in event.src_path:
                self.last_change = time.time()

    handler = MdChangeHandler()
    observer = Observer()
    observer.schedule(handler, str(DATA_DIR), recursive=True)
    log.info("Watching with watchdog (debounce=%ss, data_dir=%s)", debounce, DATA_DIR)

    last_pull = time.time()
    last_scan = time.time()

    observer.start()
    try:
        while True:
            time.sleep(1)
            now = time.time()

            if handler.last_change and (now - handler.last_change) > debounce:
                handler.last_change = 0.0
                commit_and_push(stage_dirs)

            if (now - last_pull) > pull_interval:
                last_pull = now
                pull_remote()

            if (now - last_scan) > scan_interval:
                last_scan = now
                for target in targets:
                    target.scan()

    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def run_with_polling(targets: list[SyncTarget], settings: dict) -> None:
    """Fallback: poll for changes using mtime comparison."""
    poll_interval = max(float(settings["debounce_seconds"]), 60)
    pull_interval = float(settings["pull_interval_seconds"])
    scan_interval = float(settings["scan_interval_seconds"])
    stage_dirs = get_all_stage_dirs(targets)

    log.info("Polling mode (interval=%ss)", poll_interval)

    prev_mtimes = get_md_mtimes()
    last_pull = time.time()
    last_scan = time.time()

    try:
        while True:
            time.sleep(poll_interval)
            now = time.time()

            current_mtimes = get_md_mtimes()
            if current_mtimes != prev_mtimes:
                prev_mtimes = current_mtimes
                commit_and_push(stage_dirs)

            if (now - last_pull) > pull_interval:
                last_pull = now
                pull_remote()

            if (now - last_scan) > scan_interval:
                last_scan = now
                for target in targets:
                    target.scan()

    except KeyboardInterrupt:
        pass


def main() -> None:
    cfg = load_config()
    settings = get_mesy_settings(cfg)
    target_defs = load_targets(cfg)
    targets = build_targets(target_defs)
    stage_dirs = get_all_stage_dirs(targets)

    if not targets:
        log.error("No targets configured in sync.conf. Add [target:*] sections.")
        return

    log.info("Mesy sync daemon starting (repo: %s, data: %s)", REPO_DIR, DATA_DIR)
    log.info("Targets: %s", ", ".join(t.name for t in targets))

    # Initial sync
    pull_remote()
    for target in targets:
        target.scan()
    commit_and_push(stage_dirs)

    # Choose watch method
    try:
        import watchdog  # noqa: F401
        run_with_watchdog(targets, settings)
    except ImportError:
        log.info("watchdog not installed, falling back to polling")
        run_with_polling(targets, settings)
