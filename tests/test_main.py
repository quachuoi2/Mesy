"""Tests for mesy/main.py — target building, stage dir collection, mtime scanning, main()."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.factory import build_targets, get_all_stage_dirs
from src.poll import get_md_mtimes
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.generic import GenericTarget


class TestBuildTargets:
    def test_claude_code_type_produces_claude_code_target(self, tmp_repo):
        """build_targets() creates a ClaudeCodeTarget for type='claude-code'."""
        tdef = {
            "name": "claude-code",
            "type": "claude-code",
            "source": Path("/tmp/src"),
            "repo_subdir": "claude/projects",
        }
        targets = build_targets([tdef], data_dir=tmp_repo)
        assert len(targets) == 1
        assert isinstance(targets[0], ClaudeCodeTarget)

    def test_generic_type_produces_generic_target(self, tmp_repo):
        """build_targets() creates a GenericTarget for type='generic'."""
        tdef = {
            "name": "openclaw",
            "type": "generic",
            "source": Path("/tmp/src"),
            "repo_subdir": "openclaw/main",
            "file_patterns": ["*.md"],
            "exclude": [],
        }
        targets = build_targets([tdef], data_dir=tmp_repo)
        assert len(targets) == 1
        assert isinstance(targets[0], GenericTarget)
        assert targets[0].name == "openclaw"

    def test_generic_target_inherits_file_patterns_and_exclude(self, tmp_repo):
        """build_targets() passes file_patterns and exclude through to GenericTarget."""
        tdef = {
            "name": "openclaw",
            "type": "generic",
            "source": Path("/tmp/src"),
            "repo_subdir": "openclaw/main",
            "file_patterns": ["*.md", "*.txt"],
            "exclude": ["secret.md"],
        }
        targets = build_targets([tdef], data_dir=tmp_repo)
        assert targets[0].file_patterns == ["*.md", "*.txt"]
        assert targets[0].exclude == ["secret.md"]

    def test_unknown_type_emits_warning(self, tmp_repo, caplog):
        """build_targets() logs a warning for unrecognised target types."""
        import logging
        tdef = {
            "name": "mystery",
            "type": "not-a-real-type",
            "source": Path("/tmp/src"),
            "repo_subdir": "mystery",
        }
        with caplog.at_level(logging.WARNING, logger="mesy"):
            targets = build_targets([tdef], data_dir=tmp_repo)
        assert targets == []
        assert "Unknown target type" in caplog.text

    def test_mixed_target_defs_build_correct_types(self, tmp_repo):
        """build_targets() handles a list with both claude-code and generic entries."""
        tdefs = [
            {
                "name": "claude-code",
                "type": "claude-code",
                "source": Path("/tmp/claude"),
                "repo_subdir": "claude/projects",
            },
            {
                "name": "openclaw",
                "type": "generic",
                "source": Path("/tmp/openclaw"),
                "repo_subdir": "openclaw/main",
                "file_patterns": ["*.md"],
                "exclude": [],
            },
        ]
        targets = build_targets(tdefs, data_dir=tmp_repo)
        assert len(targets) == 2
        assert isinstance(targets[0], ClaudeCodeTarget)
        assert isinstance(targets[1], GenericTarget)


class TestGetAllStageDirs:
    def test_deduplicates_identical_dirs_across_targets(self, tmp_repo):
        """get_all_stage_dirs() returns unique dirs even when targets share them."""
        t1 = MagicMock()
        t1.get_stage_dirs.return_value = ["claude/projects/", "claude/global/"]
        t2 = MagicMock()
        t2.get_stage_dirs.return_value = ["claude/projects/"]  # duplicate

        result = get_all_stage_dirs([t1, t2])
        assert result == ["claude/projects/", "claude/global/"]

    def test_preserves_order_of_first_occurrence(self, tmp_repo):
        """get_all_stage_dirs() preserves insertion order, deduplicating on first seen."""
        t1 = MagicMock()
        t1.get_stage_dirs.return_value = ["a/", "b/"]
        t2 = MagicMock()
        t2.get_stage_dirs.return_value = ["b/", "c/"]

        result = get_all_stage_dirs([t1, t2])
        assert result == ["a/", "b/", "c/"]

    def test_empty_target_list_returns_empty(self, tmp_repo):
        """get_all_stage_dirs() returns empty list for no targets."""
        assert get_all_stage_dirs([]) == []


class TestGetMdMtimes:
    def test_finds_md_files_in_repo(self, tmp_repo):
        """get_md_mtimes() returns mtimes for all .md files in REPO_DIR."""
        (tmp_repo / "notes.md").write_text("# Notes")
        (tmp_repo / "claude" / "global" / "CLAUDE.md").write_text("# Global")

        result = get_md_mtimes(tmp_repo)

        assert any("notes.md" in k for k in result)
        assert any("CLAUDE.md" in k for k in result)

    def test_skips_dotgit_directory(self, tmp_repo):
        """get_md_mtimes() does not include files under .git/."""
        git_dir = tmp_repo / ".git" / "COMMIT_EDITMSG"
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        git_dir.write_text("init")

        # Put a .md file inside .git to confirm exclusion
        (tmp_repo / ".git" / "hidden.md").write_text("should be ignored")
        (tmp_repo / "visible.md").write_text("should appear")

        result = get_md_mtimes(tmp_repo)

        assert not any(".git" in k for k in result)
        assert any("visible.md" in k for k in result)

    def test_skips_non_md_files(self, tmp_repo):
        """get_md_mtimes() ignores .txt and other non-.md files."""
        (tmp_repo / "readme.txt").write_text("text file")
        (tmp_repo / "data.md").write_text("# Data")

        result = get_md_mtimes(tmp_repo)

        assert not any("readme.txt" in k for k in result)
        assert any("data.md" in k for k in result)


class TestBuildRepoContexts:
    def test_builds_single_repo_context(self, tmp_multi_repo, monkeypatch):
        """build_repo_contexts() creates a RepoContext for each repo in the list."""
        from src.factory import build_repo_contexts

        # Setup sync.conf for repo_a
        conf_text = (
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
            "sync_mode = copy\n"
        )
        (tmp_multi_repo["repo_a"] / "sync.conf").write_text(conf_text)

        repo_list = [{"name": "repo-a", "path": tmp_multi_repo["repo_a"]}]
        contexts = build_repo_contexts(repo_list)

        assert len(contexts) == 1
        assert contexts[0].name == "repo-a"
        assert contexts[0].data_dir == tmp_multi_repo["repo_a"]
        assert len(contexts[0].targets) == 1
        assert len(contexts[0].stage_dirs) > 0

    def test_builds_multiple_repo_contexts(self, tmp_multi_repo, monkeypatch):
        """build_repo_contexts() handles multiple repos with independent configs."""
        from src.factory import build_repo_contexts

        # Setup sync.conf for both repos with different targets
        for repo_name, repo_path in [
            ("repo-a", tmp_multi_repo["repo_a"]),
            ("repo-b", tmp_multi_repo["repo_b"]),
        ]:
            conf_text = (
                "[target:generic]\n"
                f"source = {repo_path / 'openclaw' / 'main'}\n"
                "repo_subdir = openclaw/main\n"
            )
            (repo_path / "sync.conf").write_text(conf_text)

        repo_list = [
            {"name": "repo-a", "path": tmp_multi_repo["repo_a"]},
            {"name": "repo-b", "path": tmp_multi_repo["repo_b"]},
        ]
        contexts = build_repo_contexts(repo_list)

        assert len(contexts) == 2
        assert contexts[0].name == "repo-a"
        assert contexts[1].name == "repo-b"
        assert contexts[0].data_dir == tmp_multi_repo["repo_a"]
        assert contexts[1].data_dir == tmp_multi_repo["repo_b"]

    def test_preserves_target_order(self, tmp_multi_repo):
        """build_repo_contexts() preserves the order of repos."""
        from src.factory import build_repo_contexts

        # Setup sync.conf for repo_a
        conf_text = (
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )
        (tmp_multi_repo["repo_a"] / "sync.conf").write_text(conf_text)

        repo_list = [{"name": "repo-a", "path": tmp_multi_repo["repo_a"]}]
        contexts = build_repo_contexts(repo_list)

        assert contexts[0].name == "repo-a"

    def test_settings_populated_per_repo_from_sync_conf(self, tmp_multi_repo):
        """build_repo_contexts() populates ctx.settings independently per repo."""
        from src.factory import build_repo_contexts

        (tmp_multi_repo["repo_a"] / "sync.conf").write_text(
            "[mesy]\ndebounce_seconds = 10\npull_interval_seconds = 120\n"
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )
        (tmp_multi_repo["repo_b"] / "sync.conf").write_text(
            "[mesy]\ndebounce_seconds = 45\npull_interval_seconds = 300\n"
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_b'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )

        contexts = build_repo_contexts([
            {"name": "repo-a", "path": tmp_multi_repo["repo_a"]},
            {"name": "repo-b", "path": tmp_multi_repo["repo_b"]},
        ])

        assert contexts[0].settings["debounce_seconds"] == "10"
        assert contexts[0].settings["pull_interval_seconds"] == "120"
        assert contexts[1].settings["debounce_seconds"] == "45"
        assert contexts[1].settings["pull_interval_seconds"] == "300"

    def test_settings_defaults_when_no_sync_conf(self, tmp_multi_repo):
        """build_repo_contexts() uses config defaults when sync.conf has no [mesy] section."""
        from src.factory import build_repo_contexts

        # Write a sync.conf with only a target (no [mesy] override)
        (tmp_multi_repo["repo_a"] / "sync.conf").write_text(
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )

        contexts = build_repo_contexts([{"name": "repo-a", "path": tmp_multi_repo["repo_a"]}])

        assert contexts[0].settings["debounce_seconds"] == "30"
        assert contexts[0].settings["pull_interval_seconds"] == "60"


class TestRunWithWatchdog:
    def test_initializes_observer_for_each_repo(self, tmp_multi_repo, monkeypatch):
        """run_with_watchdog() schedules observers for all data repos."""
        pytest.importorskip("watchdog")
        from src.watch import run_with_watchdog
        from src.factory import RepoContext
        from unittest.mock import patch, MagicMock

        # Mock watchdog Observer class
        mock_observer = MagicMock()
        mock_observer_class = MagicMock(return_value=mock_observer)

        # Mock the time module to exit after one iteration
        call_count = [0]

        def fake_sleep(duration):
            call_count[0] += 1
            if call_count[0] > 1:
                raise KeyboardInterrupt()

        monkeypatch.setattr("src.watch.time.sleep", fake_sleep)

        # Patch watchdog.observers.Observer within run_with_watchdog
        with patch("src.watch.Observer", mock_observer_class):
            settings = {
                "debounce_seconds": "30",
                "pull_interval_seconds": "60",
                "scan_interval_seconds": "60",
            }

            # Create minimal contexts
            ctx_a = RepoContext(
                name="repo-a",
                data_dir=tmp_multi_repo["repo_a"],
                targets=[],
                stage_dirs=[],
                settings=settings,
            )
            ctx_b = RepoContext(
                name="repo-b",
                data_dir=tmp_multi_repo["repo_b"],
                targets=[],
                stage_dirs=[],
                settings=settings,
            )

            run_with_watchdog([ctx_a, ctx_b])

            # Verify observer was scheduled for both repos
            assert mock_observer.schedule.call_count == 2
            mock_observer.start.assert_called_once()
            mock_observer.join.assert_called_once()

    def test_handles_keyboard_interrupt(self, tmp_multi_repo, monkeypatch):
        """run_with_watchdog() stops observer and exits cleanly on KeyboardInterrupt."""
        pytest.importorskip("watchdog")
        from src.watch import run_with_watchdog
        from src.factory import RepoContext
        from unittest.mock import patch, MagicMock

        mock_observer = MagicMock()
        mock_observer_class = MagicMock(return_value=mock_observer)

        def fake_sleep(duration):
            raise KeyboardInterrupt()

        monkeypatch.setattr("src.watch.time.sleep", fake_sleep)

        settings = {
            "debounce_seconds": "30",
            "pull_interval_seconds": "60",
            "scan_interval_seconds": "60",
        }

        ctx = RepoContext(
            name="repo-a",
            data_dir=tmp_multi_repo["repo_a"],
            targets=[],
            stage_dirs=[],
            settings=settings,
        )

        # Should not raise, should stop observer
        with patch("src.watch.Observer", mock_observer_class):
            run_with_watchdog([ctx])

        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()


class TestRunWithPolling:
    def test_polls_on_mtime_changes(self, tmp_multi_repo, monkeypatch):
        """run_with_polling() detects .md file changes and calls commit_and_push."""
        from src.poll import run_with_polling
        from src.factory import RepoContext

        settings = {
            "debounce_seconds": "1",
            "pull_interval_seconds": "60",
            "scan_interval_seconds": "60",
        }

        ctx = RepoContext(
            name="repo-a",
            data_dir=tmp_multi_repo["repo_a"],
            targets=[],
            stage_dirs=["openclaw/main/"],
            settings=settings,
        )

        commit_mock = MagicMock()
        pull_mock = MagicMock()
        monkeypatch.setattr("src.git_sync.commit_and_push", commit_mock)
        monkeypatch.setattr("src.git_sync.pull_remote", pull_mock)

        # Mock get_md_mtimes to simulate a change on the second call
        call_count = [0]
        initial_mtimes = {"repo_a/test.md": 1000.0}
        modified_mtimes = {"repo_a/test.md": 2000.0}

        def fake_get_md_mtimes(data_dir=None):
            call_count[0] += 1
            # First call (initialization): return initial mtimes
            # Second call (first loop iteration): return modified mtimes (change detected)
            # Third call: return modified mtimes again (no change)
            if call_count[0] == 1:
                return initial_mtimes
            else:
                return modified_mtimes

        monkeypatch.setattr("src.poll.get_md_mtimes", fake_get_md_mtimes)

        sleep_count = [0]

        def fake_sleep(duration):
            sleep_count[0] += 1
            if sleep_count[0] > 2:
                # Exit after two sleep calls (initialization + first check + exit)
                raise KeyboardInterrupt()

        monkeypatch.setattr("src.poll.time.sleep", fake_sleep)

        run_with_polling([ctx])

        # commit_and_push should be called when mtime changes
        commit_mock.assert_called()

    def test_handles_keyboard_interrupt_cleanly(self, tmp_multi_repo, monkeypatch):
        """run_with_polling() exits gracefully on KeyboardInterrupt."""
        from src.poll import run_with_polling
        from src.factory import RepoContext

        settings = {
            "debounce_seconds": "1",
            "pull_interval_seconds": "60",
            "scan_interval_seconds": "60",
        }

        ctx = RepoContext(
            name="repo-a",
            data_dir=tmp_multi_repo["repo_a"],
            targets=[],
            stage_dirs=[],
            settings=settings,
        )

        def fake_sleep(duration):
            raise KeyboardInterrupt()

        monkeypatch.setattr("src.poll.time.sleep", fake_sleep)

        # Should not raise
        run_with_polling([ctx])

    def test_per_repo_pull_interval_respected(self, tmp_multi_repo, monkeypatch):
        """run_with_polling() uses each ctx's own pull_interval_seconds, not the global."""
        from src.poll import run_with_polling
        from src.factory import RepoContext

        # repo_a has a very short pull interval; repo_b has a very long one.
        ctx_a = RepoContext(
            name="repo-a",
            data_dir=tmp_multi_repo["repo_a"],
            targets=[],
            stage_dirs=[],
            settings={"debounce_seconds": "1", "pull_interval_seconds": "1", "scan_interval_seconds": "9999"},
        )
        ctx_b = RepoContext(
            name="repo-b",
            data_dir=tmp_multi_repo["repo_b"],
            targets=[],
            stage_dirs=[],
            settings={"debounce_seconds": "1", "pull_interval_seconds": "9999", "scan_interval_seconds": "9999"},
        )

        pull_mock = MagicMock()
        monkeypatch.setattr("src.git_sync.pull_remote", pull_mock)
        monkeypatch.setattr("src.git_sync.commit_and_push", MagicMock())
        monkeypatch.setattr("src.poll.get_md_mtimes", lambda data_dir=None: {})

        # Advance time between init and loop tick so pull checks fire.
        call_count = [0]

        def fake_time():
            call_count[0] += 1
            # First N calls (init of last_pull + last_scan) return t=0.
            # Later calls (inside loop) return t=100 so (now - last_pull) = 100.
            if call_count[0] <= 3:
                return 0.0
            return 100.0

        sleep_count = [0]

        def fake_sleep(duration):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                raise KeyboardInterrupt()

        monkeypatch.setattr("src.poll.time.sleep", fake_sleep)
        monkeypatch.setattr("src.poll.time.time", fake_time)

        run_with_polling([ctx_a, ctx_b])

        # pull_remote should only be called for repo-a (short interval) not repo-b (long interval).
        called_dirs = [call.kwargs.get("data_dir") or call.args[0] if call.args else call.kwargs.get("data_dir") for call in pull_mock.call_args_list]
        # Normalise: pull_remote is called as pull_remote(data_dir=ctx.data_dir)
        called_dirs = [c["data_dir"] for c in [call[1] for call in pull_mock.call_args_list]]
        assert tmp_multi_repo["repo_a"] in called_dirs
        assert tmp_multi_repo["repo_b"] not in called_dirs


class TestMain:
    def test_main_runs_initial_sync_cycle(self, tmp_repo, monkeypatch):
        """main() loads config, builds targets, calls pull_remote, scan, commit_and_push."""
        from configparser import ConfigParser
        from src.targets.base import SyncTarget

        # Write a minimal sync.conf so load_targets returns one generic target
        conf_text = (
            "[target:openclaw]\n"
            f"source = {tmp_repo / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )
        (tmp_repo / "sync.conf").write_text(conf_text)

        pull_mock = MagicMock()
        push_mock = MagicMock()
        scan_mock = MagicMock()

        monkeypatch.setattr("src.git_sync.pull_remote", pull_mock)
        monkeypatch.setattr("src.git_sync.commit_and_push", push_mock)

        # Prevent the daemon loop from starting after initial sync
        import src.factory as factory_mod

        original_build = factory_mod.build_targets

        def patched_build(target_defs, data_dir):
            targets = original_build(target_defs, data_dir=data_dir)
            for t in targets:
                t.scan = scan_mock
            return targets

        monkeypatch.setattr("src.factory.build_targets", patched_build)

        # Prevent the watchdog/polling loop from running
        monkeypatch.setattr("src.main.run_with_watchdog", lambda *a, **kw: None)
        monkeypatch.setattr("src.main.run_with_polling", lambda *a, **kw: None)

        # repos.conf is required — point it at tmp_repo.
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
        """main() uses repos.conf when available and loads all repos."""
        from src.main import main

        # Setup sync.conf for both repos
        for repo_path in [tmp_multi_repo["repo_a"], tmp_multi_repo["repo_b"]]:
            conf_text = (
                "[target:generic]\n"
                f"source = {repo_path / 'openclaw' / 'main'}\n"
                "repo_subdir = openclaw/main\n"
            )
            (repo_path / "sync.conf").write_text(conf_text)

        # Mock the watch loop
        monkeypatch.setattr("src.main.run_with_watchdog", lambda *a, **kw: None)
        monkeypatch.setattr("src.main.run_with_polling", lambda *a, **kw: None)

        # Mock git operations
        pull_mock = MagicMock()
        push_mock = MagicMock()
        monkeypatch.setattr("src.git_sync.pull_remote", pull_mock)
        monkeypatch.setattr("src.git_sync.commit_and_push", push_mock)

        # Patch load_repos to return our test repos
        monkeypatch.setattr(
            "src.main.load_repos",
            lambda: [
                {"name": "repo-a", "path": tmp_multi_repo["repo_a"]},
                {"name": "repo-b", "path": tmp_multi_repo["repo_b"]},
            ],
        )

        main()

        # Should call pull and push for both repos
        assert pull_mock.call_count == 2
        assert push_mock.call_count == 2


class TestIntervalValidation:
    """Intervals must be finite and positive; nan would stall the scan forever."""

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "abc", ""])
    def test_invalid_interval_rejected(self, tmp_repo, value):
        from src.factory import build_repo_contexts

        (tmp_repo / "sync.conf").write_text(f"[mesy]\nscan_interval_seconds = {value}\n")
        with pytest.raises(ValueError, match="scan_interval_seconds"):
            build_repo_contexts([{"name": "r", "path": tmp_repo}])

    def test_valid_interval_accepted(self, tmp_repo):
        from src.factory import build_repo_contexts

        (tmp_repo / "sync.conf").write_text("[mesy]\nscan_interval_seconds = 45\n")
        contexts = build_repo_contexts([{"name": "r", "path": tmp_repo}])
        assert contexts[0].settings["scan_interval_seconds"] == "45"


class TestStartupExitStatus:
    """Misconfiguration must exit non-zero so Restart=on-failure actually restarts."""

    def test_missing_repos_conf_exits_non_zero(self, monkeypatch):
        from src.main import main

        monkeypatch.setattr("src.main.load_repos", lambda: [])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    def test_bad_interval_exits_non_zero(self, tmp_repo, monkeypatch):
        from src.main import main

        (tmp_repo / "sync.conf").write_text("[mesy]\nscan_interval_seconds = nan\n")
        monkeypatch.setattr("src.main.load_repos",
                            lambda: [{"name": "r", "path": tmp_repo}])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


class TestScanFailureHandling:
    def test_repeated_identical_failure_logs_once(self, caplog):
        from src.factory import RepoContext
        from src.cycle import _scan_failures, scan_repo

        _scan_failures.clear()
        target = MagicMock()
        target.name = "t"
        target.scan.side_effect = ValueError("same conflict")
        ctx = RepoContext(name="r", data_dir=Path("/tmp"), targets=[target],
                          stage_dirs=[], settings={})

        with caplog.at_level("ERROR"):
            for _ in range(3):
                assert scan_repo(ctx) is False
        assert len(caplog.records) == 1

    def test_recovery_is_reported(self, caplog):
        from src.factory import RepoContext
        from src.cycle import _scan_failures, scan_repo

        _scan_failures.clear()
        target = MagicMock()
        target.name = "t"
        target.scan.side_effect = [ValueError("boom"), None]
        ctx = RepoContext(name="r", data_dir=Path("/tmp"), targets=[target],
                          stage_dirs=[], settings={})

        with caplog.at_level("INFO"):
            assert scan_repo(ctx) is False
            assert scan_repo(ctx) is True
        assert any("recovered" in r.message for r in caplog.records)

    def test_healthy_target_still_scans_when_sibling_fails(self):
        from src.factory import RepoContext
        from src.cycle import _scan_failures, scan_repo

        _scan_failures.clear()
        bad, good = MagicMock(), MagicMock()
        bad.name, good.name = "bad", "good"
        bad.scan.side_effect = ValueError("boom")
        ctx = RepoContext(name="r", data_dir=Path("/tmp"), targets=[bad, good],
                          stage_dirs=[], settings={})

        assert scan_repo(ctx) is False
        assert good.scan.call_count == 1
