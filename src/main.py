"""Mesy main loop — multi-repo, multi-target sync daemon."""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler

from src.config import MESY_DIR, load_repos
from src.cycle import commit_repo, first_sync, run_intervals, scan_repo
from src.detectors.base import ChangeDetector
from src.detectors.poll import PollingDetector
from src.factory import RepoCtx, build_repo_ctxs

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
LOOP_TICK_SECONDS = 1.0
LOG_FILE_NAME = "log.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 1
log = logging.getLogger("mesy")


def main() -> None:
    _setup_logging()
    repo_ctxs = _load_repo_ctxs()
    _log_startup(repo_ctxs)

    for repo_ctx in repo_ctxs:
        first_sync(repo_ctx)

    _run(repo_ctxs, _make_detector())


def _setup_logging() -> None:
    log_file = RotatingFileHandler(
        MESY_DIR / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    logging.basicConfig(level=logging.INFO, format=LOG_FMT, handlers=[log_file])
    log.setLevel(logging.DEBUG)


def _load_repo_ctxs() -> list[RepoCtx]:
    # Misconfiguration exits non-zero: a plain return reports success, and
    # systemd's Restart=on-failure would leave the daemon silently dead.
    repo_list = load_repos()
    if not repo_list:
        log.error(
            "repos.ini not found or empty at %s — Mesy now requires a repos.ini with at least one [repo:*] section.",
            MESY_DIR / "repos.ini",
        )
        raise SystemExit(1)

    log.info("Loaded %d repo(s) from repos.ini", len(repo_list))

    try:
        repo_ctxs = build_repo_ctxs(repo_list)

    except (ValueError, OSError) as exc:
        log.error("Configuration error: %s", exc)
        raise SystemExit(1) from None

    if not any(repo_ctx.targets for repo_ctx in repo_ctxs):
        log.error("No targets configured in any sync.ini. Add [target:*] sections.")
        raise SystemExit(1)

    return repo_ctxs


def _log_startup(repo_ctxs: list[RepoCtx]) -> None:
    log.info("Mesy sync daemon starting (repo: %s)", MESY_DIR)
    for repo_ctx in repo_ctxs:
        log.info(
            "  [%s] repo_base=%s, targets=%s",
            repo_ctx.name,
            repo_ctx.repo_base,
            ", ".join(t.name for t in repo_ctx.targets),
        )


def _make_detector() -> ChangeDetector:
    try:
        from src.detectors.watch import WatchdogDetector

    except ImportError:
        log.info("watchdog not installed, falling back to polling")
        return PollingDetector()

    return WatchdogDetector()


def _run(repo_ctxs: list[RepoCtx], detector: ChangeDetector) -> None:
    now = time.time()
    last_pull = {repo_ctx.name: now for repo_ctx in repo_ctxs}
    last_scan = dict(last_pull)

    detector.start(repo_ctxs)

    try:
        while True:
            time.sleep(LOOP_TICK_SECONDS)
            now = time.time()
            for repo_ctx in repo_ctxs:
                if detector.changed(repo_ctx, now) and scan_repo(repo_ctx):
                    commit_repo(repo_ctx)
                run_intervals(repo_ctx, now, last_pull, last_scan)

    except KeyboardInterrupt:
        pass

    finally:
        detector.stop()
