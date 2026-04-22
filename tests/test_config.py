"""Tests for mesy/config.py — config loading and target parsing."""

from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

import pytest

import src.config as config_mod
from src.config import (
    RepoContext,
    get_mesy_settings,
    load_config,
    load_project_mappings,
    load_repos,
    load_targets,
)


class TestLoadConfig:
    def test_defaults_when_conf_missing(self, tmp_repo, monkeypatch):
        """load_config() returns defaults when sync.conf does not exist."""
        # tmp_repo already patches CONFIG_PATH to a non-existent path
        cfg = load_config()
        settings = get_mesy_settings(cfg)
        assert settings["debounce_seconds"] == "30"
        assert settings["pull_interval_seconds"] == "60"
        assert settings["scan_interval_seconds"] == "60"

    def test_custom_mesy_settings(self, tmp_repo, monkeypatch):
        """Custom [mesy] values override defaults."""
        conf = tmp_repo / "sync.conf"
        conf.write_text("[mesy]\ndebounce_seconds = 10\npull_interval_seconds = 120\n")
        cfg = load_config()
        settings = get_mesy_settings(cfg)
        assert settings["debounce_seconds"] == "10"
        assert settings["pull_interval_seconds"] == "120"
        # un-overridden key keeps its default
        assert settings["scan_interval_seconds"] == "60"

    def test_parse_claude_code_target(self, tmp_repo):
        """[target:*] with mapping_conf produces type=claude-code."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:claude-code]\n"
            "source = /tmp/projects\n"
            "repo_subdir = claude/projects\n"
            "mapping_conf = claude/projects.conf\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert len(targets) == 1
        assert targets[0]["type"] == "claude-code"
        assert targets[0]["name"] == "claude-code"
        assert targets[0]["mapping_conf"] == "claude/projects.conf"

    def test_parse_generic_target(self, tmp_repo):
        """[target:*] without mapping_conf produces type=generic."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert len(targets) == 1
        assert targets[0]["type"] == "generic"
        assert targets[0]["name"] == "openclaw"

    def test_parse_file_patterns(self, tmp_repo):
        """file_patterns list is parsed from comma-separated config value."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "file_patterns = *.md, *.txt\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["file_patterns"] == ["*.md", "*.txt"]

    def test_default_file_patterns(self, tmp_repo):
        """file_patterns defaults to ['*.md'] when not specified."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["file_patterns"] == ["*.md"]

    def test_parse_exclude_list(self, tmp_repo):
        """exclude list is parsed from comma-separated config value."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "exclude = TOOLS.md, HEARTBEAT.md\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["exclude"] == ["TOOLS.md", "HEARTBEAT.md"]


class TestSyncMode:
    def test_sync_mode_defaults_to_symlink(self, tmp_repo):
        """sync_mode defaults to 'symlink' when not specified in config."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["sync_mode"] == "symlink"

    def test_sync_mode_accepts_copy(self, tmp_repo):
        """sync_mode = copy is accepted and stored as-is."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "sync_mode = copy\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["sync_mode"] == "copy"

    def test_sync_mode_explicit_symlink(self, tmp_repo):
        """sync_mode = symlink is accepted explicitly."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "sync_mode = symlink\n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["sync_mode"] == "symlink"

    def test_sync_mode_invalid_warns_and_defaults_to_symlink(self, tmp_repo, caplog):
        """Invalid sync_mode emits a warning and falls back to 'symlink'."""
        import logging

        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "sync_mode = hardlink\n"
        )
        cfg = load_config()
        with caplog.at_level(logging.WARNING, logger="mesy"):
            targets = load_targets(cfg)

        assert targets[0]["sync_mode"] == "symlink"
        assert any("Invalid sync_mode" in r.message for r in caplog.records)

    def test_sync_mode_invalid_message_includes_value(self, tmp_repo, caplog):
        """Warning message for invalid sync_mode includes the bad value."""
        import logging

        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "sync_mode = rsync\n"
        )
        cfg = load_config()
        with caplog.at_level(logging.WARNING, logger="mesy"):
            load_targets(cfg)

        assert any("rsync" in r.message for r in caplog.records)

    def test_sync_mode_whitespace_is_stripped(self, tmp_repo):
        """sync_mode values with surrounding whitespace are accepted."""
        conf = tmp_repo / "sync.conf"
        conf.write_text(
            "[target:openclaw]\n"
            "source = /tmp/workspace\n"
            "repo_subdir = openclaw/main\n"
            "sync_mode =  copy  \n"
        )
        cfg = load_config()
        targets = load_targets(cfg)
        assert targets[0]["sync_mode"] == "copy"


class TestLoadRepos:
    def test_returns_empty_when_no_repos_conf(self, tmp_repo, monkeypatch):
        """load_repos() returns [] when repos.conf does not exist."""
        # tmp_repo patches REPOS_CONF_PATH to tmp_repo/repos.conf which doesn't exist
        result = load_repos()
        assert result == []

    def test_parses_valid_repos(self, tmp_path, monkeypatch):
        """load_repos() returns a list of dicts with name and path for each [repo:*] section."""
        repo_dir = tmp_path / "DataRepo"
        repo_dir.mkdir()

        repos_conf = tmp_path / "repos.conf"
        repos_conf.write_text(
            "[repo:my-data]\n"
            f"path = {repo_dir}\n"
        )
        monkeypatch.setattr("src.config.REPOS_CONF_PATH", repos_conf)

        result = load_repos()
        assert len(result) == 1
        assert result[0]["name"] == "my-data"
        assert result[0]["path"] == repo_dir

    def test_parses_multiple_repos(self, tmp_path, monkeypatch):
        """load_repos() returns one entry per [repo:*] section."""
        dir_a = tmp_path / "A"
        dir_a.mkdir()
        dir_b = tmp_path / "B"
        dir_b.mkdir()

        repos_conf = tmp_path / "repos.conf"
        repos_conf.write_text(
            "[repo:alpha]\n"
            f"path = {dir_a}\n"
            "\n"
            "[repo:beta]\n"
            f"path = {dir_b}\n"
        )
        monkeypatch.setattr("src.config.REPOS_CONF_PATH", repos_conf)

        result = load_repos()
        names = [r["name"] for r in result]
        assert "alpha" in names
        assert "beta" in names
        assert len(result) == 2

    def test_skips_non_existent_path(self, tmp_path, monkeypatch, caplog):
        """load_repos() skips entries whose path does not exist and logs a warning."""
        import logging

        repos_conf = tmp_path / "repos.conf"
        repos_conf.write_text(
            "[repo:missing]\n"
            "path = /nonexistent/path/that/does/not/exist\n"
        )
        monkeypatch.setattr("src.config.REPOS_CONF_PATH", repos_conf)

        with caplog.at_level(logging.WARNING, logger="mesy"):
            result = load_repos()

        assert result == []
        assert any("missing" in r.message for r in caplog.records)

    def test_skips_section_without_path(self, tmp_path, monkeypatch, caplog):
        """load_repos() skips [repo:*] sections that have no 'path' key."""
        import logging

        repos_conf = tmp_path / "repos.conf"
        repos_conf.write_text("[repo:empty-section]\n")
        monkeypatch.setattr("src.config.REPOS_CONF_PATH", repos_conf)

        with caplog.at_level(logging.WARNING, logger="mesy"):
            result = load_repos()

        assert result == []

    def test_ignores_non_repo_sections(self, tmp_path, monkeypatch):
        """load_repos() only reads [repo:*] sections, ignoring others."""
        repo_dir = tmp_path / "DataRepo"
        repo_dir.mkdir()

        repos_conf = tmp_path / "repos.conf"
        repos_conf.write_text(
            "[general]\n"
            "some_key = some_value\n"
            "\n"
            "[repo:real]\n"
            f"path = {repo_dir}\n"
        )
        monkeypatch.setattr("src.config.REPOS_CONF_PATH", repos_conf)

        result = load_repos()
        assert len(result) == 1
        assert result[0]["name"] == "real"


class TestRepoContext:
    def test_instantiation_with_required_fields(self, tmp_path):
        """RepoContext can be created with name and data_dir; targets/stage_dirs default empty."""
        ctx = RepoContext(name="test-repo", data_dir=tmp_path)
        assert ctx.name == "test-repo"
        assert ctx.data_dir == tmp_path
        assert ctx.targets == []
        assert ctx.stage_dirs == []

    def test_instantiation_with_all_fields(self, tmp_path):
        """RepoContext stores targets and stage_dirs when provided."""
        from unittest.mock import MagicMock
        mock_target = MagicMock()
        ctx = RepoContext(
            name="full-repo",
            data_dir=tmp_path,
            targets=[mock_target],
            stage_dirs=["global/", "projects/"],
        )
        assert len(ctx.targets) == 1
        assert ctx.stage_dirs == ["global/", "projects/"]


class TestLoadProjectMappings:
    def test_load_project_mappings(self, tmp_repo):
        """Mappings from projects.conf are parsed correctly."""
        conf = tmp_repo / "claude" / "projects.conf"
        conf.write_text(
            "[Workspace]\n"
            "paths = -home-user-Workspace, -home-user2-Workspace\n"
        )
        mappings = load_project_mappings()
        assert mappings["-home-user-Workspace"] == "Workspace"
        assert mappings["-home-user2-Workspace"] == "Workspace"

    def test_missing_projects_conf(self, tmp_repo):
        """load_project_mappings() returns empty dict when projects.conf missing."""
        mappings = load_project_mappings()
        assert mappings == {}
