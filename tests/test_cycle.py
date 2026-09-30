"""Tests for src/cycle.py — scan failure handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


class TestScanFailureHandling:
    def test_repeated_identical_failure_logs_once(self, caplog):
        from src.factory import RepoCtx
        from src.cycle import _scan_failures, scan_repo

        _scan_failures.clear()
        target = MagicMock()
        target.name = "t"
        target.scan.side_effect = ValueError("same conflict")
        repo_ctx = RepoCtx(name="r", repo_base=Path("/tmp"), targets=[target],
                          stage_dirs=[], settings={})

        with caplog.at_level("ERROR"):
            for _ in range(3):
                assert scan_repo(repo_ctx) is False
        assert len(caplog.records) == 1

    def test_recovery_is_reported(self, caplog):
        from src.factory import RepoCtx
        from src.cycle import _scan_failures, scan_repo

        _scan_failures.clear()
        target = MagicMock()
        target.name = "t"
        target.scan.side_effect = [ValueError("boom"), None]
        repo_ctx = RepoCtx(name="r", repo_base=Path("/tmp"), targets=[target],
                          stage_dirs=[], settings={})

        with caplog.at_level("INFO"):
            assert scan_repo(repo_ctx) is False
            assert scan_repo(repo_ctx) is True
        assert any("recovered" in r.message for r in caplog.records)

    def test_healthy_target_still_scans_when_sibling_fails(self):
        from src.factory import RepoCtx
        from src.cycle import _scan_failures, scan_repo

        _scan_failures.clear()
        bad, good = MagicMock(), MagicMock()
        bad.name, good.name = "bad", "good"
        bad.scan.side_effect = ValueError("boom")
        repo_ctx = RepoCtx(name="r", repo_base=Path("/tmp"), targets=[bad, good],
                          stage_dirs=[], settings={})

        assert scan_repo(repo_ctx) is False
        assert good.scan.call_count == 1
