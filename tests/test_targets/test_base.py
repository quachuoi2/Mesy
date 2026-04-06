"""Tests for mesy/targets/base.py — ABC enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.targets.base import SyncTarget


class TestSyncTargetABC:
    def test_cannot_instantiate_directly(self):
        """SyncTarget cannot be instantiated directly (it's an ABC)."""
        with pytest.raises(TypeError):
            SyncTarget(name="test", source=Path("/tmp"), repo_dir=Path("/tmp/repo"))

    def test_subclass_must_implement_abstract_methods(self):
        """Subclass that doesn't implement all abstract methods cannot be instantiated."""

        class IncompleteTarget(SyncTarget):
            def setup(self):
                pass

            def scan(self):
                pass

            # Missing get_stage_dirs

        with pytest.raises(TypeError):
            IncompleteTarget(name="incomplete", source=Path("/tmp"), repo_dir=Path("/tmp/repo"))

    def test_get_watched_paths_default(self, tmp_path):
        """get_watched_paths() returns [source, repo_dir] by default."""

        class ConcreteTarget(SyncTarget):
            def setup(self):
                pass

            def scan(self):
                pass

            def get_stage_dirs(self):
                return ["test/"]

        source = tmp_path / "source"
        repo_dir = tmp_path / "repo"
        target = ConcreteTarget(name="test", source=source, repo_dir=repo_dir)

        watched = target.get_watched_paths()
        assert source in watched
        assert repo_dir in watched
