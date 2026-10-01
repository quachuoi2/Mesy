"""Target-specific fixtures for test_targets/."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.sync_state import SyncState


@pytest.fixture(autouse=True)
def isolated_sync_state(tmp_path, monkeypatch):
    """Give each test its own SyncState so hash tracking doesn't bleed between tests.

    Every SyncState created during the test (e.g. by build_modes) shares the
    fixture's backing file and in-memory _hashes dict, so any hash a target writes
    is immediately visible via isolated_sync_state.get_hash().
    """
    fresh_state = SyncState(hash_file_path=tmp_path / ".mesy_sync_hashes.json")

    original_init = SyncState.__init__

    def patched_init(self_inner, hash_file_path: Path) -> None:
        # Point every new SyncState at the same backing file as the fixture state.
        original_init(self_inner, hash_file_path=fresh_state._hash_file_path)
        # Share the same in-memory dict so writes by any instance are visible to
        # the fixture object immediately (without needing to reload from disk).
        self_inner._hashes = fresh_state._hashes

    monkeypatch.setattr(SyncState, "__init__", patched_init)
    return fresh_state


@pytest.fixture
def claude_env(tmp_path, tmp_repo, monkeypatch):
    """Full Claude Code test environment with injected paths."""
    claude_dir = tmp_path / "claude"
    projects_dir = claude_dir / "projects"
    scratch_dir = tmp_path / "scratch"

    claude_dir.mkdir(parents=True)
    projects_dir.mkdir(parents=True)
    scratch_dir.mkdir(parents=True)

    return {
        "repo": tmp_repo,
        "claude_dir": claude_dir,
        "projects_dir": projects_dir,
        "scratch_dir": scratch_dir,
    }


@pytest.fixture
def generic_env(tmp_path, tmp_repo):
    """Source + target_dir directories for GenericTarget tests."""
    src = tmp_path / "source"
    src.mkdir()

    target_dir = tmp_repo / "openclaw" / "main"
    target_dir.mkdir(parents=True, exist_ok=True)

    return {"src": src, "target_dir": target_dir, "tmp_repo": tmp_repo}
