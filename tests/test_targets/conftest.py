"""Target-specific fixtures for test_targets/."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def claude_env(tmp_path, tmp_repo, monkeypatch):
    """Full Claude Code test environment with injected paths."""
    claude_dir = tmp_path / "claude"
    projects_dir = claude_dir / "projects"
    pending_dir = tmp_repo / "projects" / ".pending"

    claude_dir.mkdir(parents=True)
    projects_dir.mkdir(parents=True)

    return {
        "repo": tmp_repo,
        "claude_dir": claude_dir,
        "projects_dir": projects_dir,
        "pending_dir": pending_dir,
    }


@pytest.fixture
def generic_env(tmp_path, tmp_repo):
    """Source + repo_dir directories for GenericTarget tests."""
    source = tmp_path / "source"
    source.mkdir()
    repo_dir = tmp_repo / "openclaw" / "main"
    repo_dir.mkdir(parents=True, exist_ok=True)
    return {"source": source, "repo_dir": repo_dir, "tmp_repo": tmp_repo}
