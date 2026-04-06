"""Shared fixtures for the Mesy test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """Create a temp Mesy repo structure and monkeypatch REPO_DIR everywhere."""
    repo = tmp_path / "Mesy"
    (repo / "global").mkdir(parents=True)
    (repo / "projects").mkdir(parents=True)
    (repo / "openclaw" / "main").mkdir(parents=True)

    # Patch in config (the single source of truth)
    monkeypatch.setattr("src.config.REPO_DIR", repo)
    monkeypatch.setattr("src.config.CONFIG_PATH", repo / "sync.conf")
    monkeypatch.setattr("src.config.PROJECTS_CONF_PATH", repo / "projects.conf")

    # Propagate to all modules that import REPO_DIR directly
    monkeypatch.setattr("src.git_utils.REPO_DIR", repo)
    monkeypatch.setattr("src.main.REPO_DIR", repo)
    monkeypatch.setattr("src.targets.claude_code.REPO_DIR", repo)

    return repo


@pytest.fixture
def mock_git(monkeypatch):
    """Patch subprocess.run to capture git calls, returns a MagicMock with call recorder."""
    calls = []
    results: dict[tuple, subprocess.CompletedProcess] = {}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        key = tuple(cmd)
        if key in results:
            return results[key]
        # Build a generic successful result
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    mock = MagicMock()
    mock.calls = calls
    mock.results = results
    mock.set_result = lambda cmd_tuple, rc, stdout="", stderr="": results.__setitem__(
        cmd_tuple, subprocess.CompletedProcess(list(cmd_tuple), rc, stdout, stderr)
    )

    monkeypatch.setattr("subprocess.run", fake_run)
    return mock


@pytest.fixture
def tmp_source(tmp_path):
    """Create a temp source directory with sample .md files."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "notes.md").write_text("# Notes\nSome content.")
    (src / "todo.md").write_text("# Todo\n- item1\n- item2")
    (src / "ignore.txt").write_text("not a markdown file")
    return src
