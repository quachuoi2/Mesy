"""Tests for mesy/targets/base.py — ABC enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.targets.base import SyncTarget


class TestSyncTargetABC:
    def test_cannot_instantiate_directly(self):
        """SyncTarget cannot be instantiated directly (it's an ABC)."""
        with pytest.raises(TypeError):
            SyncTarget(name="test", src=Path("/tmp"), target_dir=Path("/tmp/repo"))

    def test_subclass_must_implement_abstract_methods(self):
        """Subclass that doesn't implement all abstract methods cannot be instantiated."""

        class IncompleteTarget(SyncTarget):
            def setup(self):
                pass

            def scan(self):
                pass

            # Missing get_stage_dirs

        with pytest.raises(TypeError):
            IncompleteTarget(name="incomplete", src=Path("/tmp"), target_dir=Path("/tmp/repo"))
