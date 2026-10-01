"""Configuration loading and target definitions for Mesy."""

from __future__ import annotations

import logging
import os
from configparser import ConfigParser
from pathlib import Path

log = logging.getLogger("mesy")

MESY_DIR = Path(__file__).resolve().parent.parent
REPOS_CFG_PATH = MESY_DIR / "repos.ini"

SETTING_DEFAULTS = {
    "debounce_seconds": "30",
    "pull_interval_seconds": "60",
    "scan_interval_seconds": "60",
}
SYNC_MODES = {"symlink", "copy"}
DEFAULT_SYNC_MODE = "symlink"
DEFAULT_FILE_PATTERNS = ["*.md"]


def load_repos() -> list[dict]:
    """Parse repos.ini and return a list of repo dicts.

    Each dict has keys: name (str), path (Path). Returns an empty list if
    repos.ini does not exist — the daemon requires at least one [repo:*]
    section and will exit if the list is empty.
    """
    if not REPOS_CFG_PATH.exists():
        return []

    repos_cfg = ConfigParser()
    repos_cfg.read(REPOS_CFG_PATH)

    repos = [_parse_repo(repos_cfg, section) for section in repos_cfg.sections() if section.startswith("repo:")]
    return [repo for repo in repos if repo is not None]


def _parse_repo(repos_cfg: ConfigParser, section: str) -> dict | None:
    name = section.split(":", 1)[1].strip()
    raw_path = repos_cfg.get(section, "path", fallback="").strip()
    if not raw_path:
        log.warning("[repos.ini] Section [%s] has no 'path' key, skipping", section)
        return None

    path = Path(raw_path).expanduser().resolve()
    if not path.is_dir():
        log.warning(
            "[repos.ini] Repo '%s' path=%s is not a valid directory, skipping",
            name,
            path,
        )
        return None

    return {"name": name, "path": path}


def load_sync_cfg(repo_base: Path) -> ConfigParser:
    """Load sync.ini from repo_base and return the full ConfigParser."""
    sync_cfg_path = repo_base / "sync.ini"

    sync_cfg = ConfigParser()
    sync_cfg.read_dict({"mesy": SETTING_DEFAULTS})
    if sync_cfg_path.exists():
        sync_cfg.read(sync_cfg_path)
    return sync_cfg


def get_mesy_settings(sync_cfg: ConfigParser) -> dict:
    """Extract the [mesy] section as a dict."""
    return dict(sync_cfg["mesy"])


def load_project_mappings(repo_base: Path) -> dict[str, str]:
    """Load projects.ini from repo_base and return {raw_claude_name: canonical_name} mapping."""
    projects_cfg_path = repo_base / "claude" / "projects.ini"
    mapping = {}
    if not projects_cfg_path.exists():
        return mapping

    projects_cfg = ConfigParser()
    projects_cfg.read(projects_cfg_path)

    for section in projects_cfg.sections():
        canonical = section
        paths_str = projects_cfg.get(section, "paths", fallback="")
        for raw_name in paths_str.split(","):
            raw_name = raw_name.strip()
            if raw_name:
                mapping[raw_name] = canonical

    return mapping


def load_targets(sync_cfg: ConfigParser) -> list[dict]:
    """Parse [target:*] sections from config into target definitions.

    Returns a list of dicts with keys: name, type, source, repo_subdir,
    and optional: mapping_cfg, file_patterns, exclude, sync_mode.
    """
    return [_parse_target(sync_cfg, section) for section in sync_cfg.sections() if section.startswith("target:")]


def _parse_target(sync_cfg: ConfigParser, section: str) -> dict:
    name = section.split(":", 1)[1]
    target_def = {"name": name}
    target_def["type"] = _target_type(sync_cfg, section)
    target_def["source"] = _target_src(sync_cfg, section, target_def["type"])
    target_def["repo_subdir"] = sync_cfg.get(section, "repo_subdir")
    if sync_cfg.has_option(section, "mapping_cfg"):
        target_def["mapping_cfg"] = sync_cfg.get(section, "mapping_cfg")
    target_def["file_patterns"] = _list_option(sync_cfg, section, "file_patterns", list(DEFAULT_FILE_PATTERNS))
    target_def["exclude"] = _list_option(sync_cfg, section, "exclude", [])
    target_def["sync_mode"] = _sync_mode(sync_cfg, section)
    return target_def


def _target_type(sync_cfg: ConfigParser, section: str) -> str:
    inferred_type = "claude-code" if sync_cfg.has_option(section, "mapping_cfg") else "generic"
    target_type = sync_cfg.get(section, "type", fallback=inferred_type).strip()
    if target_type not in {"claude-code", "generic", "codex"}:
        raise ValueError(f"[{section}] Unknown target type: {target_type}")

    return target_type


def _target_src(sync_cfg: ConfigParser, section: str, target_type: str) -> Path:
    if target_type == "codex":
        src = sync_cfg.get(section, "source", fallback=os.environ.get("CODEX_HOME", "~/.codex"))
    else:
        src = sync_cfg.get(section, "source")
    if not src.strip():
        raise ValueError(f"[{section}] source cannot be empty")

    return Path(src).expanduser()


def _list_option(sync_cfg: ConfigParser, section: str, option: str, default: list[str]) -> list[str]:
    if not sync_cfg.has_option(section, option):
        return default

    return [item.strip() for item in sync_cfg.get(section, option).split(",")]


def _sync_mode(sync_cfg: ConfigParser, section: str) -> str:
    sync_mode = sync_cfg.get(section, "sync_mode", fallback=DEFAULT_SYNC_MODE).strip()
    if sync_mode not in SYNC_MODES:
        raise ValueError(f"[{section}] Invalid sync_mode {sync_mode!r}; must be one of {sorted(SYNC_MODES)}")

    return sync_mode
