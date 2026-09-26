"""Mesy main loop — multi-repo, multi-target sync daemon."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from src.config import REPO_DIR, load_repos
from src.cycle import first_sync
from src.factory import RepoContext, build_repo_contexts
from src.poll import run_with_polling
from src.watch import run_with_watchdog

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
log = logging.getLogger("mesy")


def main() -> None:
    _setup_logging()
    repo_contexts = _load_repo_contexts()
    _log_startup(repo_contexts)
    for ctx in repo_contexts:
        first_sync(ctx)
    _watch(repo_contexts)


def _setup_logging() -> None:
    log_file = RotatingFileHandler(REPO_DIR / "log.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format=LOG_FMT, handlers=[log_file])


def _load_repo_contexts() -> list[RepoContext]:
    # Misconfiguration exits non-zero: a plain return reports success, and
    # systemd's Restart=on-failure would leave the daemon silently dead.
    repo_list = load_repos()
    if not repo_list:
        log.error(
            "repos.conf not found or empty at %s — Mesy now requires a repos.conf with at least one [repo:*] section.",
            REPO_DIR / "repos.conf",
        )
        raise SystemExit(1)

    log.info("Loaded %d repo(s) from repos.conf", len(repo_list))
    try:
        repo_contexts = build_repo_contexts(repo_list)
    except (ValueError, OSError) as exc:
        log.error("Configuration error: %s", exc)
        raise SystemExit(1) from None

    if not any(ctx.targets for ctx in repo_contexts):
        log.error("No targets configured in any sync.conf. Add [target:*] sections.")
        raise SystemExit(1)
    return repo_contexts


def _log_startup(repo_contexts: list[RepoContext]) -> None:
    log.info("Mesy sync daemon starting (repo: %s)", REPO_DIR)
    for ctx in repo_contexts:
        log.info(
            "  [%s] data_dir=%s, targets=%s",
            ctx.name,
            ctx.data_dir,
            ", ".join(t.name for t in ctx.targets),
        )


def _watch(repo_contexts: list[RepoContext]) -> None:
    try:
        import watchdog  # noqa: F401
        run_with_watchdog(repo_contexts)
    except ImportError:
        log.info("watchdog not installed, falling back to polling")
        run_with_polling(repo_contexts)
