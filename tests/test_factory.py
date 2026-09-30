"""Tests for src/factory.py — target building, stage dirs, repo contexts, interval checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.factory import build_targets, get_all_stage_dirs
from src.targets.claude_code import ClaudeCodeTarget
from src.targets.generic import GenericTarget


class TestBuildTargets:
    def test_claude_code_type_produces_claude_code_target(self, tmp_repo):
        """build_targets() creates a ClaudeCodeTarget for type='claude-code'."""
        target_def = {
            "name": "claude-code",
            "type": "claude-code",
            "source": Path("/tmp/src"),
            "repo_subdir": "claude/projects",
        }
        targets = build_targets([target_def], repo_base=tmp_repo)
        assert len(targets) == 1
        assert isinstance(targets[0], ClaudeCodeTarget)

    def test_generic_type_produces_generic_target(self, tmp_repo):
        """build_targets() creates a GenericTarget for type='generic'."""
        target_def = {
            "name": "openclaw",
            "type": "generic",
            "source": Path("/tmp/src"),
            "repo_subdir": "openclaw/main",
            "file_patterns": ["*.md"],
            "exclude": [],
        }
        targets = build_targets([target_def], repo_base=tmp_repo)
        assert len(targets) == 1
        assert isinstance(targets[0], GenericTarget)
        assert targets[0].name == "openclaw"

    def test_generic_target_inherits_file_patterns_and_exclude(self, tmp_repo):
        """build_targets() passes file_patterns and exclude through to GenericTarget."""
        target_def = {
            "name": "openclaw",
            "type": "generic",
            "source": Path("/tmp/src"),
            "repo_subdir": "openclaw/main",
            "file_patterns": ["*.md", "*.txt"],
            "exclude": ["secret.md"],
        }
        targets = build_targets([target_def], repo_base=tmp_repo)
        assert targets[0].file_patterns == ["*.md", "*.txt"]
        assert targets[0].exclude == ["secret.md"]

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
        targets = build_targets(tdefs, repo_base=tmp_repo)
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


class TestBuildRepoCtxs:
    def test_builds_single_repo_context(self, tmp_multi_repo, monkeypatch):
        """build_repo_ctxs() creates a RepoCtx for each repo in the list."""
        from src.factory import build_repo_ctxs

        sync_cfg_text = (
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
            "sync_mode = copy\n"
        )
        (tmp_multi_repo["repo_a"] / "sync.ini").write_text(sync_cfg_text)

        repo_list = [{"name": "repo-a", "path": tmp_multi_repo["repo_a"]}]
        contexts = build_repo_ctxs(repo_list)

        assert len(contexts) == 1
        assert contexts[0].name == "repo-a"
        assert contexts[0].repo_base == tmp_multi_repo["repo_a"]
        assert len(contexts[0].targets) == 1
        assert len(contexts[0].stage_dirs) > 0

    def test_builds_multiple_repo_ctxs(self, tmp_multi_repo, monkeypatch):
        """build_repo_ctxs() handles multiple repos with independent configs."""
        from src.factory import build_repo_ctxs

        for repo_name, repo_path in [
            ("repo-a", tmp_multi_repo["repo_a"]),
            ("repo-b", tmp_multi_repo["repo_b"]),
        ]:
            sync_cfg_text = (
                "[target:generic]\n"
                f"source = {repo_path / 'openclaw' / 'main'}\n"
                "repo_subdir = openclaw/main\n"
            )
            (repo_path / "sync.ini").write_text(sync_cfg_text)

        repo_list = [
            {"name": "repo-a", "path": tmp_multi_repo["repo_a"]},
            {"name": "repo-b", "path": tmp_multi_repo["repo_b"]},
        ]
        contexts = build_repo_ctxs(repo_list)

        assert len(contexts) == 2
        assert contexts[0].name == "repo-a"
        assert contexts[1].name == "repo-b"
        assert contexts[0].repo_base == tmp_multi_repo["repo_a"]
        assert contexts[1].repo_base == tmp_multi_repo["repo_b"]

    def test_preserves_target_order(self, tmp_multi_repo):
        """build_repo_ctxs() preserves the order of repos."""
        from src.factory import build_repo_ctxs

        sync_cfg_text = (
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )
        (tmp_multi_repo["repo_a"] / "sync.ini").write_text(sync_cfg_text)

        repo_list = [{"name": "repo-a", "path": tmp_multi_repo["repo_a"]}]
        contexts = build_repo_ctxs(repo_list)

        assert contexts[0].name == "repo-a"

    def test_settings_populated_per_repo_from_sync_cfg(self, tmp_multi_repo):
        """build_repo_ctxs() populates repo_ctx.settings independently per repo."""
        from src.factory import build_repo_ctxs

        (tmp_multi_repo["repo_a"] / "sync.ini").write_text(
            "[mesy]\ndebounce_seconds = 10\npull_interval_seconds = 120\n"
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )
        (tmp_multi_repo["repo_b"] / "sync.ini").write_text(
            "[mesy]\ndebounce_seconds = 45\npull_interval_seconds = 300\n"
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_b'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )

        contexts = build_repo_ctxs([
            {"name": "repo-a", "path": tmp_multi_repo["repo_a"]},
            {"name": "repo-b", "path": tmp_multi_repo["repo_b"]},
        ])

        assert contexts[0].settings["debounce_seconds"] == "10"
        assert contexts[0].settings["pull_interval_seconds"] == "120"
        assert contexts[1].settings["debounce_seconds"] == "45"
        assert contexts[1].settings["pull_interval_seconds"] == "300"

    def test_settings_defaults_when_no_sync_cfg(self, tmp_multi_repo):
        """build_repo_ctxs() uses config defaults when sync.ini has no [mesy] section."""
        from src.factory import build_repo_ctxs

        (tmp_multi_repo["repo_a"] / "sync.ini").write_text(
            "[target:generic]\n"
            f"source = {tmp_multi_repo['repo_a'] / 'openclaw' / 'main'}\n"
            "repo_subdir = openclaw/main\n"
        )

        contexts = build_repo_ctxs([{"name": "repo-a", "path": tmp_multi_repo["repo_a"]}])

        assert contexts[0].settings["debounce_seconds"] == "30"
        assert contexts[0].settings["pull_interval_seconds"] == "60"


class TestIntervalValidation:
    """Intervals must be finite and positive; nan would stall the scan forever."""

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "abc", ""])
    def test_invalid_interval_rejected(self, tmp_repo, value):
        from src.factory import build_repo_ctxs

        (tmp_repo / "sync.ini").write_text(f"[mesy]\nscan_interval_seconds = {value}\n")

        with pytest.raises(ValueError, match="scan_interval_seconds"):
            build_repo_ctxs([{"name": "r", "path": tmp_repo}])

    def test_valid_interval_accepted(self, tmp_repo):
        from src.factory import build_repo_ctxs

        (tmp_repo / "sync.ini").write_text("[mesy]\nscan_interval_seconds = 45\n")

        contexts = build_repo_ctxs([{"name": "r", "path": tmp_repo}])
        assert contexts[0].settings["scan_interval_seconds"] == "45"
