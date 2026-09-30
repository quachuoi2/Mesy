"""Tests for src/detectors/ — modified-time scanning, watchdog and polling detectors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.detectors.poll import get_md_modified_times


class TestGetMdModifiedTimes:
    def test_finds_md_files_in_repo(self, tmp_repo):
        """get_md_modified_times() returns last-modified times for all .md files in MESY_DIR."""
        (tmp_repo / "notes.md").write_text("# Notes")
        (tmp_repo / "claude" / "global" / "CLAUDE.md").write_text("# Global")

        result = get_md_modified_times(tmp_repo)

        assert any("notes.md" in k for k in result)
        assert any("CLAUDE.md" in k for k in result)

    def test_skips_dotgit_directory(self, tmp_repo):
        """get_md_modified_times() does not include files under .git/."""
        git_dir = tmp_repo / ".git" / "COMMIT_EDITMSG"
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        git_dir.write_text("init")

        # Put a .md file inside .git to confirm exclusion
        (tmp_repo / ".git" / "hidden.md").write_text("should be ignored")
        (tmp_repo / "visible.md").write_text("should appear")

        result = get_md_modified_times(tmp_repo)

        assert not any(".git" in k for k in result)
        assert any("visible.md" in k for k in result)

    def test_skips_non_md_files(self, tmp_repo):
        """get_md_modified_times() ignores .txt and other non-.md files."""
        (tmp_repo / "readme.txt").write_text("text file")
        (tmp_repo / "data.md").write_text("# Data")

        result = get_md_modified_times(tmp_repo)

        assert not any("readme.txt" in k for k in result)
        assert any("data.md" in k for k in result)


class TestWatchdogDetector:
    def _start(self, repo_ctxs, monkeypatch):
        pytest.importorskip("watchdog")
        from src.detectors.watch import WatchdogDetector

        observer = MagicMock()
        monkeypatch.setattr("src.detectors.watch.Observer", lambda: observer)
        monkeypatch.setattr("src.detectors.watch.time.time", lambda: 100.0)
        detector = WatchdogDetector()
        detector.start(repo_ctxs)
        return detector, observer

    def test_schedules_observer_for_each_repo(self, tmp_multi_repo, repo_settings, monkeypatch):
        """start() schedules one watch per data repo and starts the observer."""
        from src.factory import RepoCtx

        ctx_a = RepoCtx(name="repo-a", repo_base=tmp_multi_repo["repo_a"], settings=repo_settings)
        ctx_b = RepoCtx(name="repo-b", repo_base=tmp_multi_repo["repo_b"], settings=repo_settings)

        _, observer = self._start([ctx_a, ctx_b], monkeypatch)

        assert observer.schedule.call_count == 2
        observer.start.assert_called_once()

    def test_changed_waits_for_debounce_and_reports_once(self, tmp_multi_repo, repo_settings, monkeypatch):
        """changed() is True once the repo has been quiet for debounce_seconds, then resets."""
        from types import SimpleNamespace
        from src.factory import RepoCtx

        repo = tmp_multi_repo["repo_a"]
        repo_ctx = RepoCtx(name="repo-a", repo_base=repo, settings=repo_settings)
        detector, observer = self._start([repo_ctx], monkeypatch)
        handler = observer.schedule.call_args.args[0]

        handler.on_any_event(SimpleNamespace(is_directory=False, src_path=str(repo / "notes.md")))

        assert not detector.changed(repo_ctx, 120.0)
        assert detector.changed(repo_ctx, 131.0)
        assert not detector.changed(repo_ctx, 200.0)

    def test_rename_onto_md_file_counts_as_change(self, tmp_multi_repo, repo_settings, monkeypatch):
        """A save that writes a temp file and renames it onto a .md file marks the repo changed."""
        from types import SimpleNamespace
        from src.factory import RepoCtx

        repo = tmp_multi_repo["repo_a"]
        repo_ctx = RepoCtx(name="repo-a", repo_base=repo, settings=repo_settings)
        detector, observer = self._start([repo_ctx], monkeypatch)
        handler = observer.schedule.call_args.args[0]

        handler.on_any_event(SimpleNamespace(
            is_directory=False, src_path=str(repo / ".notes.tmp"), dest_path=str(repo / "notes.md"),
        ))

        assert detector.changed(repo_ctx, 131.0)


class TestPollingDetector:
    def test_reports_modified_time_change_after_poll_interval(self, tmp_multi_repo, repo_settings, monkeypatch):
        """changed() rescans only after the poll interval and reports each modified-time change once."""
        from src.detectors.poll import POLL_MIN_SECONDS, PollingDetector
        from src.factory import RepoCtx

        modified_times = iter([{"a.md": 1.0}, {"a.md": 2.0}, {"a.md": 2.0}])
        monkeypatch.setattr("src.detectors.poll.get_md_modified_times", lambda repo_base: next(modified_times))
        monkeypatch.setattr("src.detectors.poll.time.time", lambda: 0.0)
        repo_ctx = RepoCtx(name="repo-a", repo_base=tmp_multi_repo["repo_a"], settings=repo_settings)
        detector = PollingDetector()
        detector.start([repo_ctx])

        assert not detector.changed(repo_ctx, POLL_MIN_SECONDS - 1)
        assert detector.changed(repo_ctx, POLL_MIN_SECONDS)
        assert not detector.changed(repo_ctx, 2 * POLL_MIN_SECONDS)
