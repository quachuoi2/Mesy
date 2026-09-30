"""Tests for src/main.py — run loop, main() and startup exit status."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _interrupt_after_sleeps(sleeps_before_stop: int):
    count = [0]

    def fake_sleep(_):
        count[0] += 1
        if count[0] > sleeps_before_stop:
            raise KeyboardInterrupt()

    return fake_sleep


class TestRun:
    def test_commits_when_detector_reports_change(self, tmp_multi_repo, repo_settings, monkeypatch):
        """_run() scans and commits a repo when its detector reports a change."""
        from src.factory import RepoCtx
        from src.main import _run

        commit_mock = MagicMock()
        monkeypatch.setattr("src.git_sync.commit_and_push", commit_mock)
        monkeypatch.setattr("src.git_sync.pull_remote", MagicMock())
        monkeypatch.setattr("src.main.time.sleep", _interrupt_after_sleeps(1))
        repo_ctx = RepoCtx(name="repo-a", repo_base=tmp_multi_repo["repo_a"],
                           stage_dirs=["openclaw/main/"], settings=repo_settings)
        detector = MagicMock()
        detector.changed.return_value = True

        _run([repo_ctx], detector)

        commit_mock.assert_called_once()

    def test_stops_detector_on_keyboard_interrupt(self, tmp_multi_repo, repo_settings, monkeypatch):
        """_run() exits cleanly on KeyboardInterrupt and stops the detector."""
        from src.factory import RepoCtx
        from src.main import _run

        monkeypatch.setattr("src.main.time.sleep", _interrupt_after_sleeps(0))
        repo_ctx = RepoCtx(name="repo-a", repo_base=tmp_multi_repo["repo_a"], settings=repo_settings)
        detector = MagicMock()

        _run([repo_ctx], detector)

        detector.start.assert_called_once_with([repo_ctx])
        detector.stop.assert_called_once()

    def test_per_repo_pull_interval_respected(self, tmp_multi_repo, monkeypatch):
        """_run() uses each repo_ctx's own pull_interval_seconds."""
        from src.factory import RepoCtx
        from src.main import _run

        ctx_a = RepoCtx(name="repo-a", repo_base=tmp_multi_repo["repo_a"],
                        settings={"pull_interval_seconds": "1", "scan_interval_seconds": "9999"})
        ctx_b = RepoCtx(name="repo-b", repo_base=tmp_multi_repo["repo_b"],
                        settings={"pull_interval_seconds": "9999", "scan_interval_seconds": "9999"})
        pull_mock = MagicMock()
        monkeypatch.setattr("src.git_sync.pull_remote", pull_mock)
        monkeypatch.setattr("src.main.time.time", MagicMock(side_effect=[0.0, 100.0]))
        monkeypatch.setattr("src.main.time.sleep", _interrupt_after_sleeps(1))
        detector = MagicMock()
        detector.changed.return_value = False

        _run([ctx_a, ctx_b], detector)

        pulled = [call.kwargs["repo_base"] for call in pull_mock.call_args_list]
        assert pulled == [tmp_multi_repo["repo_a"]]


class TestMain:
    def test_main_runs_initial_sync_cycle(self, tmp_repo, monkeypatch):
        """main() loads config, builds targets, calls pull_remote, scan, commit_and_push."""
        from configparser import ConfigParser
        from src.targets.base import SyncTarget

        sync_cfg_text = (
            "[target:openclaw]\n"
            f"source = {tmp_repo / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )
        (tmp_repo / "sync.ini").write_text(sync_cfg_text)

        pull_mock = MagicMock()
        push_mock = MagicMock()
        scan_mock = MagicMock()

        monkeypatch.setattr("src.git_sync.pull_remote", pull_mock)
        monkeypatch.setattr("src.git_sync.commit_and_push", push_mock)

        import src.factory as factory_mod

        original_build = factory_mod.build_targets

        def patched_build(target_defs, repo_base):
            targets = original_build(target_defs, repo_base=repo_base)
            for t in targets:
                t.scan = scan_mock
            return targets

        monkeypatch.setattr("src.factory.build_targets", patched_build)

        # Prevent the watchdog/polling loop from running
        monkeypatch.setattr("src.main._run", lambda *a, **kw: None)

        # repos.ini is required — point it at tmp_repo.
        monkeypatch.setattr(
            "src.main.load_repos",
            lambda: [{"name": "default", "path": tmp_repo}],
        )

        from src.main import main

        main()

        pull_mock.assert_called_once()
        scan_mock.assert_called()
        push_mock.assert_called_once()

    def test_main_multi_repo_mode(self, tmp_multi_repo, monkeypatch):
        """main() uses repos.ini when available and loads all repos."""
        from src.main import main

        for repo_path in [tmp_multi_repo["repo_a"], tmp_multi_repo["repo_b"]]:
            sync_cfg_text = (
                "[target:generic]\n"
                f"source = {repo_path / 'openclaw' / 'main'}\n"
                "repo_subdir = openclaw/main\n"
            )
            (repo_path / "sync.ini").write_text(sync_cfg_text)

        # Mock the watch loop
        monkeypatch.setattr("src.main._run", lambda *a, **kw: None)

        pull_mock = MagicMock()
        push_mock = MagicMock()
        monkeypatch.setattr("src.git_sync.pull_remote", pull_mock)
        monkeypatch.setattr("src.git_sync.commit_and_push", push_mock)

        monkeypatch.setattr(
            "src.main.load_repos",
            lambda: [
                {"name": "repo-a", "path": tmp_multi_repo["repo_a"]},
                {"name": "repo-b", "path": tmp_multi_repo["repo_b"]},
            ],
        )

        main()

        assert pull_mock.call_count == 2
        assert push_mock.call_count == 2


class TestStartupExitStatus:
    """Misconfiguration must exit non-zero so Restart=on-failure actually restarts."""

    def test_missing_repos_cfg_exits_non_zero(self, monkeypatch):
        from src.main import main

        monkeypatch.setattr("src.main.load_repos", lambda: [])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    def test_bad_interval_exits_non_zero(self, tmp_repo, monkeypatch):
        from src.main import main

        (tmp_repo / "sync.ini").write_text("[mesy]\nscan_interval_seconds = nan\n")
        monkeypatch.setattr("src.main.load_repos",
                            lambda: [{"name": "r", "path": tmp_repo}])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
