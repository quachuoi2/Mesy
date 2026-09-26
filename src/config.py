"""Configuration loading and target definitions for Mesy."""

from __future__ import annotations

import logging
import os
from configparser import ConfigParser
from pathlib import Path

log = logging.getLogger("mesy")

REPO_DIR = Path(__file__).resolve().parent.parent
REPOS_CONF_PATH = REPO_DIR / "repos.conf"


def load_repos() -> list[dict]:
    """Parse repos.conf and return a list of repo dicts.

    Each dict has keys: name (str), path (Path). Returns an empty list if
    repos.conf does not exist — the daemon requires at least one [repo:*]
    section and will exit if the list is empty.
    """
    if not REPOS_CONF_PATH.exists():
        return []

    cfg = ConfigParser()
    cfg.read(REPOS_CONF_PATH)

    repos = [_parse_repo(cfg, section) for section in cfg.sections() if section.startswith("repo:")]
    return [repo for repo in repos if repo is not None]


def _parse_repo(cfg: ConfigParser, section: str) -> dict | None:
    name = section.split(":", 1)[1].strip()
    raw_path = cfg.get(section, "path", fallback="").strip()
    if not raw_path:
        log.warning("[repos.conf] Section [%s] has no 'path' key, skipping", section)
        return None
    path = Path(raw_path).expanduser().resolve()
    if not path.is_dir():
        log.warning(
            "[repos.conf] Repo '%s' path=%s is not a valid directory, skipping",
            name,
            path,
        )
        return None
    return {"name": name, "path": path}


def load_config(data_dir: Path) -> ConfigParser:
    """Load sync.conf from data_dir and return the full ConfigParser."""
    config_path = data_dir / "sync.conf"
    defaults = {
        "debounce_seconds": "30",
        "pull_interval_seconds": "60",
        "scan_interval_seconds": "60",
    }
    cfg = ConfigParser()
    cfg.read_dict({"mesy": defaults})
    if config_path.exists():
        cfg.read(config_path)
    return cfg


def get_mesy_settings(cfg: ConfigParser) -> dict:
    """Extract the [mesy] section as a dict."""
    return dict(cfg["mesy"])


def load_project_mappings(data_dir: Path) -> dict[str, str]:
    """Claude Code memory config loading"""
    """Load projects.conf from data_dir and return {raw_claude_name: canonical_name} mapping."""
    projects_conf = data_dir / "claude" / "projects.conf"
    mapping = {}
    if not projects_conf.exists():
        return mapping
    cfg = ConfigParser()
    cfg.read(projects_conf)
    for section in cfg.sections():
        if section == "mesy":
            continue
        canonical = section
        paths_str = cfg.get(section, "paths", fallback="")
        for raw_name in paths_str.split(","):
            raw_name = raw_name.strip()
            if raw_name:
                mapping[raw_name] = canonical
    return mapping


def load_targets(cfg: ConfigParser) -> list[dict]:
    """Parse [target:*] sections from config into target definitions.

    Returns a list of dicts with keys: name, type, source, repo_subdir,
    and optional: mapping_conf, file_patterns, exclude, sync_mode.
    """
    return [_parse_target(cfg, section) for section in cfg.sections() if section.startswith("target:")]


def _parse_target(cfg: ConfigParser, section: str) -> dict:
    name = section.split(":", 1)[1]
    target_def = {"name": name}
    target_def["type"] = _target_type(cfg, section)
    target_def["source"] = _target_source(cfg, section, target_def["type"])
    target_def["repo_subdir"] = cfg.get(section, "repo_subdir")
    if cfg.has_option(section, "mapping_conf"):
        target_def["mapping_conf"] = cfg.get(section, "mapping_conf")
    target_def["file_patterns"] = _list_option(cfg, section, "file_patterns", ["*.md"])
    target_def["exclude"] = _list_option(cfg, section, "exclude", [])
    target_def["sync_mode"] = _sync_mode(cfg, section, name)
    return target_def


def _target_type(cfg: ConfigParser, section: str) -> str:
    inferred_type = "claude-code" if cfg.has_option(section, "mapping_conf") else "generic"
    target_type = cfg.get(section, "type", fallback=inferred_type).strip()
    if target_type not in {"claude-code", "generic", "codex"}:
        raise ValueError(f"[{section}] Unknown target type: {target_type}")
    return target_type


def _target_source(cfg: ConfigParser, section: str, target_type: str) -> Path:
    if target_type == "codex":
        source = cfg.get(section, "source", fallback=os.environ.get("CODEX_HOME", "~/.codex"))
    else:
        source = cfg.get(section, "source")
    if not source.strip():
        raise ValueError(f"[{section}] source cannot be empty")
    return Path(source).expanduser()


def _list_option(cfg: ConfigParser, section: str, option: str, default: list[str]) -> list[str]:
    if not cfg.has_option(section, option):
        return default
    return [item.strip() for item in cfg.get(section, option).split(",")]


def _sync_mode(cfg: ConfigParser, section: str, name: str) -> str:
    sync_mode = cfg.get(section, "sync_mode", fallback="symlink").strip()
    valid_sync_modes = {"symlink", "copy"}
    if sync_mode not in valid_sync_modes:
        log.warning(
            "[target:%s] Invalid sync_mode=%r; must be one of %s — defaulting to 'symlink'",
            name,
            sync_mode,
            sorted(valid_sync_modes),
        )
        sync_mode = "symlink"
    return sync_mode
