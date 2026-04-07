"""Tests for mesy/targets/claude_code.py — project discovery, mapping, pending, CLAUDE.md sync."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.targets.claude_code import ClaudeCodeTarget


def make_target(claude_env) -> ClaudeCodeTarget:
    """Helper to build a ClaudeCodeTarget with injected paths."""
    return ClaudeCodeTarget(
        source=claude_env["projects_dir"],
        repo_dir=claude_env["repo"] / "claude" / "projects",
        claude_dir=claude_env["claude_dir"],
        projects_dir=claude_env["projects_dir"],
        pending_dir=claude_env["pending_dir"],
    )


class TestSyncGlobalClaudeMd:
    def test_copies_and_symlinks_global_claude_md(self, claude_env, monkeypatch):
        """scan() copies global CLAUDE.md to repo and replaces it with a symlink."""
        global_dir = claude_env["repo"] / "claude" / "global"
        local_claude = claude_env["claude_dir"] / "CLAUDE.md"
        local_claude.write_text("# Global Config")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: {})

        target = make_target(claude_env)
        target.scan()

        repo_claude = global_dir / "CLAUDE.md"
        assert repo_claude.exists()
        assert repo_claude.read_text() == "# Global Config"
        assert local_claude.is_symlink()
        assert local_claude.resolve() == repo_claude.resolve()

    def test_skips_if_already_symlinked(self, claude_env, monkeypatch):
        """scan() skips global CLAUDE.md if it's already a symlink."""
        global_dir = claude_env["repo"] / "claude" / "global"
        repo_claude = global_dir / "CLAUDE.md"
        repo_claude.write_text("# Already in repo")

        local_claude = claude_env["claude_dir"] / "CLAUDE.md"
        local_claude.symlink_to(repo_claude)

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: {})

        target = make_target(claude_env)
        target.scan()

        # Symlink should remain as-is, not double-linked
        assert local_claude.is_symlink()
        assert local_claude.resolve() == repo_claude.resolve()

    def test_symlinks_from_repo_when_no_local(self, claude_env, monkeypatch):
        """scan() creates symlink from repo to local when only repo file exists."""
        global_dir = claude_env["repo"] / "claude" / "global"
        repo_claude = global_dir / "CLAUDE.md"
        repo_claude.write_text("# Repo-only global CLAUDE.md")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: {})

        target = make_target(claude_env)
        target.scan()

        local_claude = claude_env["claude_dir"] / "CLAUDE.md"
        assert local_claude.is_symlink()


class TestScanProjects:
    def test_adopts_mapped_project(self, claude_env, monkeypatch):
        """scan() migrates memory to canonical dir and creates symlink for a mapped project."""
        raw_name = "-home-user-MyProject"
        mapping = {raw_name: "MyProject"}

        # Create project with memory files
        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "notes.md").write_text("# Notes")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: mapping)

        target = make_target(claude_env)
        target.scan()

        canonical_dir = claude_env["repo"] / "claude" / "projects" / "MyProject"
        assert (canonical_dir / "notes.md").exists()
        assert memory_dir.is_symlink()
        assert memory_dir.resolve() == canonical_dir.resolve()

    def test_stages_unmapped_project_in_pending(self, claude_env, monkeypatch):
        """scan() stages unmapped project memory in .pending with .needs_mapping marker."""
        raw_name = "-home-user-UnknownProject"

        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "data.md").write_text("some data")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: {})

        target = make_target(claude_env)
        target.scan()

        pending_dest = claude_env["pending_dir"] / raw_name
        assert pending_dest.exists()
        assert (pending_dest / "data.md").exists()
        assert (pending_dest / ".needs_mapping").exists()
        assert memory_dir.is_symlink()

    def test_pending_marker_contains_raw_name(self, claude_env, monkeypatch):
        """The .needs_mapping marker file contains the raw project name."""
        raw_name = "-home-user-SomeProject"
        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "file.md").write_text("content")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: {})

        target = make_target(claude_env)
        target.scan()

        marker = (claude_env["pending_dir"] / raw_name / ".needs_mapping").read_text()
        assert raw_name in marker


class TestPromotePending:
    def test_promotes_pending_to_canonical(self, claude_env, monkeypatch):
        """_promote_pending() moves pending project files to canonical dir."""
        raw_name = "-home-user-NewProject"
        mapping = {raw_name: "NewProject"}

        pending_dir = claude_env["pending_dir"] / raw_name
        pending_dir.mkdir(parents=True)
        (pending_dir / "file.md").write_text("pending data")
        (pending_dir / ".needs_mapping").write_text("raw_name: " + raw_name)

        # Simulate that the project memory symlink is already in place
        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        memory_dir_symlink = project_dir / "memory"

        # Replace memory with actual symlink to pending
        shutil.rmtree(str(memory_dir))
        memory_dir.symlink_to(pending_dir)

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: mapping)

        target = make_target(claude_env)
        target._promote_pending(mapping)

        canonical_dir = claude_env["repo"] / "claude" / "projects" / "NewProject"
        assert (canonical_dir / "file.md").exists()
        assert not pending_dir.exists()

    def test_skips_pending_without_mapping(self, claude_env, monkeypatch):
        """_promote_pending() leaves pending dirs that still have no mapping."""
        raw_name = "-home-user-StillUnmapped"
        pending_dir = claude_env["pending_dir"] / raw_name
        pending_dir.mkdir(parents=True)
        (pending_dir / "file.md").write_text("content")

        target = make_target(claude_env)
        target._promote_pending({})

        assert pending_dir.exists()


class TestMigrateRepoToCanonical:
    def test_renames_raw_dir_to_canonical(self, claude_env, monkeypatch):
        """_migrate_repo_to_canonical() renames a raw-named dir to canonical name."""
        raw_name = "-home-user-OldProject"
        mapping = {raw_name: "OldProject"}

        projects_dir = claude_env["repo"] / "claude" / "projects"
        raw_dir = projects_dir / raw_name
        raw_dir.mkdir(parents=True)
        (raw_dir / "notes.md").write_text("# Old Notes")

        target = make_target(claude_env)
        target._migrate_repo_to_canonical(mapping)

        canonical_dir = projects_dir / "OldProject"
        assert canonical_dir.exists()
        assert (canonical_dir / "notes.md").exists()
        assert not raw_dir.exists()

    def test_skips_when_canonical_already_exists(self, claude_env):
        """_migrate_repo_to_canonical() skips renaming if canonical dir already exists."""
        raw_name = "-home-user-Project"
        mapping = {raw_name: "Project"}

        projects_dir = claude_env["repo"] / "claude" / "projects"
        raw_dir = projects_dir / raw_name
        raw_dir.mkdir(parents=True)
        canonical_dir = projects_dir / "Project"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "existing.md").write_text("already here")

        target = make_target(claude_env)
        target._migrate_repo_to_canonical(mapping)

        assert raw_dir.exists()  # not renamed since canonical already exists


class TestSyncClaudeMd:
    def test_copies_and_symlinks_project_claude_md(self, claude_env, monkeypatch):
        """_sync_claude_md() copies project CLAUDE.md to repo and symlinks it."""
        raw_name = "-home-user-Project"
        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        local_claude = project_dir / "CLAUDE.md"
        local_claude.write_text("# Project Config")

        repo_dir = claude_env["repo"] / "claude" / "projects" / "Project"
        repo_dir.mkdir(parents=True)

        target = make_target(claude_env)
        target._sync_claude_md(project_dir, repo_dir)

        repo_claude = repo_dir / "CLAUDE.md"
        assert repo_claude.exists()
        assert local_claude.is_symlink()
        assert local_claude.resolve() == repo_claude.resolve()

    def test_skips_if_already_symlinked(self, claude_env):
        """_sync_claude_md() does nothing if CLAUDE.md is already a symlink."""
        raw_name = "-home-user-Project"
        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)

        repo_dir = claude_env["repo"] / "claude" / "projects" / "Project"
        repo_dir.mkdir(parents=True)
        repo_claude = repo_dir / "CLAUDE.md"
        repo_claude.write_text("# In Repo")

        local_claude = project_dir / "CLAUDE.md"
        local_claude.symlink_to(repo_claude)

        target = make_target(claude_env)
        target._sync_claude_md(project_dir, repo_dir)

        # Symlink should still resolve to the same place
        assert local_claude.is_symlink()
        assert local_claude.resolve() == repo_claude.resolve()


class TestSafetyVerification:
    def test_rmtree_skipped_on_failed_migration(self, claude_env, monkeypatch, caplog):
        """rmtree is NOT called when migration verification fails."""
        import logging

        raw_name = "-home-user-BrokenProject"
        mapping = {raw_name: "BrokenProject"}

        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "important.md").write_text("critical data")

        # _verify_migration will fail because we'll make _migrate_memory_to_dir do nothing
        def fake_migrate(src, dst):
            pass  # Don't actually copy files

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda: mapping)
        target = make_target(claude_env)
        monkeypatch.setattr(target, "_migrate_memory_to_dir", fake_migrate)

        with caplog.at_level(logging.ERROR, logger="mesy"):
            target.scan()

        # memory_dir should NOT have been deleted
        assert memory_dir.exists()
        assert not memory_dir.is_symlink()
        assert any("verification failed" in r.message for r in caplog.records)


class TestGetStageDirs:
    def test_returns_correct_dirs(self, claude_env):
        """get_stage_dirs() returns ['global/', 'projects/']."""
        target = make_target(claude_env)
        assert target.get_stage_dirs() == ["claude/global/", "claude/projects/"]
