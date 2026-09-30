"""Mesy main loop — multi-repo, multi-target sync daemon."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from src.config import MESY_DIR, load_repos
from src.cycle import first_sync
from src.factory import RepoCtx, build_repo_ctxs
from src.poll import run_with_polling
from src.watch import run_with_watchdog

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
log = logging.getLogger("mesy")


def main() -> None:
    _setup_logging()
    repo_ctxs = _load_repo_ctxs()
    _log_startup(repo_ctxs)
    for repo_ctx in repo_ctxs:
        first_sync(repo_ctx)
    _watch(repo_ctxs)


def _setup_logging() -> None:
    log_file = RotatingFileHandler(MESY_DIR / "log.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format=LOG_FMT, handlers=[log_file])


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


def _watch(repo_ctxs: list[RepoCtx]) -> None:
    try:
        import watchdog  # noqa: F401
        run_with_watchdog(repo_ctxs)
    except ImportError:
        log.info("watchdog not installed, falling back to polling")
        run_with_polling(repo_ctxs)
