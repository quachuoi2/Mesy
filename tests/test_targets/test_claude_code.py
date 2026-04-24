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
        data_dir=claude_env["repo"],
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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)

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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)

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

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)
        target = make_target(claude_env)
        monkeypatch.setattr(target, "_migrate_memory_to_dir", fake_migrate)

        with caplog.at_level(logging.ERROR, logger="mesy"):
            target.scan()

        # memory_dir should NOT have been deleted
        assert memory_dir.exists()
        assert not memory_dir.is_symlink()
        assert any("verification failed" in r.message for r in caplog.records)


def make_copy_target(claude_env) -> ClaudeCodeTarget:
    """Helper to build a ClaudeCodeTarget in copy mode."""
    return ClaudeCodeTarget(
        source=claude_env["projects_dir"],
        repo_dir=claude_env["repo"] / "claude" / "projects",
        data_dir=claude_env["repo"],
        claude_dir=claude_env["claude_dir"],
        projects_dir=claude_env["projects_dir"],
        pending_dir=claude_env["pending_dir"],
        sync_mode="copy",
    )


class TestSyncFileCopy:
    def test_local_to_repo_copy(self, claude_env, monkeypatch):
        """_sync_file_copy copies local file to repo when repo is missing."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        local = claude_env["claude_dir"] / "CLAUDE.md"
        local.write_text("# Local content")
        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"

        target._sync_file_copy(local, repo, label="test file")

        assert repo.exists()
        assert repo.read_text() == "# Local content"
        assert not local.is_symlink()

    def test_repo_to_local_copy(self, claude_env, monkeypatch):
        """_sync_file_copy copies repo file to local when local is missing."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        global_dir = claude_env["repo"] / "claude" / "global"
        repo = global_dir / "CLAUDE.md"
        repo.write_text("# Repo content")
        local = claude_env["claude_dir"] / "CLAUDE.md"

        target._sync_file_copy(local, repo, label="test file")

        assert local.exists()
        assert local.read_text() == "# Repo content"
        assert not local.is_symlink()

    def test_symlink_migrated_to_real_file(self, claude_env, monkeypatch):
        """_sync_file_copy resolves a leftover symlink to a real file."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        real_file = claude_env["claude_dir"] / "real_content.md"
        real_file.write_text("# Real content")
        local = claude_env["claude_dir"] / "CLAUDE.md"
        local.symlink_to(real_file)
        assert local.is_symlink()

        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"

        target._sync_file_copy(local, repo, label="test file")

        assert not local.is_symlink()
        assert local.is_file()
        assert local.read_text() == "# Real content"
        assert repo.read_text() == "# Real content"

    def test_symlink_with_missing_target_restored_from_repo(self, claude_env, monkeypatch):
        """_sync_file_copy replaces broken symlink with copy from repo."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        local = claude_env["claude_dir"] / "CLAUDE.md"
        local.symlink_to(claude_env["claude_dir"] / "nonexistent.md")
        assert local.is_symlink()
        assert not local.exists()

        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        repo.write_text("# Repo fallback")

        target._sync_file_copy(local, repo, label="test file")

        assert not local.is_symlink()
        assert local.read_text() == "# Repo fallback"

    def test_conflict_delegates_to_merge(self, claude_env, monkeypatch):
        """_sync_file_copy calls merge_conflicting_file when both files differ."""
        merge_calls = []

        def fake_merge(local, repo):
            merge_calls.append((local, repo))

        monkeypatch.setattr("src.targets.claude_code.merge_conflicting_file", fake_merge)
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        local = claude_env["claude_dir"] / "CLAUDE.md"
        local.write_text("# Local version")
        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        repo.write_text("# Repo version (different)")

        target._sync_file_copy(local, repo, label="test file")

        assert len(merge_calls) == 1
        assert merge_calls[0][0] == local
        assert merge_calls[0][1] == repo

    def test_broken_symlink_no_repo_fallback_skipped(self, claude_env, monkeypatch, caplog):
        """_sync_file_copy skips a broken symlink when there is no repo file to restore from."""
        import logging

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        local = claude_env["claude_dir"] / "CLAUDE.md"
        local.symlink_to(claude_env["claude_dir"] / "ghost.md")

        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        # repo does NOT exist

        with caplog.at_level(logging.WARNING, logger="mesy"):
            target._sync_file_copy(local, repo, label="test file")

        assert not repo.exists()


class TestCopyDirFiles:
    def test_copies_all_files_from_source_to_dest(self, claude_env, monkeypatch):
        """_copy_dir_files copies every non-directory file into dest_dir."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        source_dir = claude_env["repo"] / "claude" / "projects" / "SomeProject"
        source_dir.mkdir(parents=True)
        (source_dir / "notes.md").write_text("# Notes")
        (source_dir / "todo.md").write_text("# Todo")

        dest_dir = claude_env["projects_dir"] / "-home-user-SomeProject" / "memory"
        dest_dir.mkdir(parents=True)

        target._copy_dir_files(source_dir, dest_dir)

        assert (dest_dir / "notes.md").read_text() == "# Notes"
        assert (dest_dir / "todo.md").read_text() == "# Todo"

    def test_skips_subdirectories(self, claude_env, monkeypatch):
        """_copy_dir_files does not recurse into subdirectories."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        source_dir = claude_env["repo"] / "claude" / "projects" / "SomeProject"
        source_dir.mkdir(parents=True)
        sub = source_dir / "subdir"
        sub.mkdir()
        (sub / "nested.md").write_text("# Nested")
        (source_dir / "top.md").write_text("# Top")

        dest_dir = claude_env["projects_dir"] / "-home-user-SomeProject" / "memory"
        dest_dir.mkdir(parents=True)

        target._copy_dir_files(source_dir, dest_dir)

        assert (dest_dir / "top.md").exists()
        assert not (dest_dir / "subdir").exists()

    def test_conflict_delegates_to_merge(self, claude_env, monkeypatch):
        """_copy_dir_files calls merge_conflicting_file for files that differ."""
        merge_calls = []

        def fake_merge(local, repo):
            merge_calls.append((local, repo))

        monkeypatch.setattr("src.targets.claude_code.merge_conflicting_file", fake_merge)
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        source_dir = claude_env["repo"] / "claude" / "projects" / "SomeProject"
        source_dir.mkdir(parents=True)
        (source_dir / "notes.md").write_text("# Source version")

        dest_dir = claude_env["projects_dir"] / "-home-user-SomeProject" / "memory"
        dest_dir.mkdir(parents=True)
        (dest_dir / "notes.md").write_text("# Dest version (different)")

        target._copy_dir_files(source_dir, dest_dir)

        assert len(merge_calls) == 1

    def test_creates_dest_dir_if_missing(self, claude_env, monkeypatch):
        """_copy_dir_files creates dest_dir when it doesn't exist yet."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        source_dir = claude_env["repo"] / "claude" / "projects" / "NewProject"
        source_dir.mkdir(parents=True)
        (source_dir / "file.md").write_text("# New")

        dest_dir = claude_env["projects_dir"] / "new_memory"
        assert not dest_dir.exists()

        target._copy_dir_files(source_dir, dest_dir)

        assert dest_dir.exists()
        assert (dest_dir / "file.md").exists()


class TestCopyModeGlobalClaudeMd:
    def test_copy_mode_syncs_local_to_repo(self, claude_env, monkeypatch):
        """copy mode: _sync_global_claude_md copies local to repo without symlinking."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        local_claude = claude_env["claude_dir"] / "CLAUDE.md"
        local_claude.write_text("# Global Config")

        target = make_copy_target(claude_env)
        target.scan()

        repo_claude = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        assert repo_claude.read_text() == "# Global Config"
        assert not local_claude.is_symlink()

    def test_copy_mode_syncs_repo_to_local(self, claude_env, monkeypatch):
        """copy mode: _sync_global_claude_md copies repo to local when local missing."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        repo_claude = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        repo_claude.write_text("# Repo Config")

        target = make_copy_target(claude_env)
        target.scan()

        local_claude = claude_env["claude_dir"] / "CLAUDE.md"
        assert local_claude.exists()
        assert not local_claude.is_symlink()
        assert local_claude.read_text() == "# Repo Config"


class TestCopyModeAdoptProject:
    def test_copy_mode_adopted_memory_dir_not_symlinked(self, claude_env, monkeypatch):
        """copy mode: mapped project memory is mirrored but memory_dir stays a real directory."""
        raw_name = "-home-user-MyProject"
        mapping = {raw_name: "MyProject"}

        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "notes.md").write_text("# Notes")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)

        target = make_copy_target(claude_env)
        target.scan()

        canonical_dir = claude_env["repo"] / "claude" / "projects" / "MyProject"
        assert (canonical_dir / "notes.md").exists()
        # In copy mode memory_dir must NOT become a symlink
        assert not memory_dir.is_symlink()
        assert memory_dir.is_dir()

    def test_copy_mode_unmapped_memory_dir_not_symlinked(self, claude_env, monkeypatch):
        """copy mode: unmapped project files are staged in .pending but memory_dir is kept."""
        raw_name = "-home-user-UnknownProject"

        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "data.md").write_text("some data")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

        target = make_copy_target(claude_env)
        target.scan()

        pending_dest = claude_env["pending_dir"] / raw_name
        assert pending_dest.exists()
        assert (pending_dest / "data.md").exists()
        # In copy mode memory_dir must NOT become a symlink
        assert not memory_dir.is_symlink()
        assert memory_dir.is_dir()


class TestSymlinkToCopyMigration:
    def test_symlinked_memory_dir_converted_to_real_dir_in_copy_mode(
        self, claude_env, monkeypatch
    ):
        """copy mode: a symlinked memory_dir for a mapped project is replaced with a real
        directory whose contents match the canonical repo directory."""
        raw_name = "-home-user-MigrateMe"
        mapping = {raw_name: "MigrateMe"}

        # Canonical dir exists in repo with some files
        canonical_dir = claude_env["repo"] / "claude" / "projects" / "MigrateMe"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "notes.md").write_text("# Canonical notes")
        (canonical_dir / "todo.md").write_text("# Canonical todo")

        # Project has a memory_dir that is already a symlink (from previous symlink adoption)
        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        memory_dir = project_dir / "memory"
        memory_dir.symlink_to(canonical_dir)
        assert memory_dir.is_symlink()

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)

        target = make_copy_target(claude_env)
        target.scan()

        # memory_dir must now be a real directory, not a symlink
        assert not memory_dir.is_symlink()
        assert memory_dir.is_dir()
        # Files from canonical dir must be present inside the real memory_dir
        assert (memory_dir / "notes.md").read_text() == "# Canonical notes"
        assert (memory_dir / "todo.md").read_text() == "# Canonical todo"

    def test_symlinked_memory_dir_unchanged_in_symlink_mode(self, claude_env, monkeypatch):
        """symlink mode: a memory_dir that is already a symlink remains a symlink."""
        raw_name = "-home-user-AlreadyLinked"
        mapping = {raw_name: "AlreadyLinked"}

        canonical_dir = claude_env["repo"] / "claude" / "projects" / "AlreadyLinked"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "notes.md").write_text("# Notes")

        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        memory_dir = project_dir / "memory"
        memory_dir.symlink_to(canonical_dir)
        assert memory_dir.is_symlink()

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)

        # Use symlink-mode target
        target = make_target(claude_env)
        target.scan()

        # Symlink must remain a symlink pointing at the canonical dir
        assert memory_dir.is_symlink()
        assert memory_dir.resolve() == canonical_dir.resolve()

    def test_symlinked_memory_dir_unmapped_left_alone_in_copy_mode(
        self, claude_env, monkeypatch
    ):
        """copy mode: a symlinked memory_dir for an unmapped project is left untouched."""
        raw_name = "-home-user-UnmappedLinked"

        # Simulate some arbitrary symlink target (e.g., a pending dir)
        pending_dest = claude_env["pending_dir"] / raw_name
        pending_dest.mkdir(parents=True)
        (pending_dest / "data.md").write_text("# Pending data")

        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        memory_dir = project_dir / "memory"
        memory_dir.symlink_to(pending_dest)
        assert memory_dir.is_symlink()

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

        target = make_copy_target(claude_env)
        target.scan()

        # Unmapped symlinked memory_dir must remain as-is
        assert memory_dir.is_symlink()
        assert memory_dir.resolve() == pending_dest.resolve()


    def test_dangling_symlink_falls_back_to_canonical_dir(self, claude_env, monkeypatch, caplog):
        """copy mode: when a symlinked memory_dir points at a missing target, files are
        restored from the canonical repo dir and a warning is logged."""
        import logging

        raw_name = "-home-user-DanglingProject"
        mapping = {raw_name: "DanglingProject"}

        # Canonical dir exists in repo with files
        canonical_dir = claude_env["repo"] / "claude" / "projects" / "DanglingProject"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "restored.md").write_text("# From canonical")

        # memory_dir is a symlink pointing at a path that no longer exists (dangling)
        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        memory_dir = project_dir / "memory"
        gone_target = claude_env["repo"] / "claude" / "projects" / "GoneTempDir"
        memory_dir.symlink_to(gone_target)
        assert memory_dir.is_symlink()
        assert not memory_dir.exists()  # dangling

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)

        target = make_copy_target(claude_env)
        with caplog.at_level(logging.WARNING, logger="mesy"):
            target.scan()

        # memory_dir must now be a real directory with files from canonical_dir
        assert not memory_dir.is_symlink()
        assert memory_dir.is_dir()
        assert (memory_dir / "restored.md").read_text() == "# From canonical"
        assert any("Symlink target missing" in r.message for r in caplog.records)

    def test_dangling_symlink_with_no_canonical_dir_logs_warning_and_creates_empty_dir(
        self, claude_env, monkeypatch, caplog
    ):
        """copy mode: when a symlinked memory_dir is dangling AND canonical_dir is also
        missing, an empty real directory is created and a warning is logged."""
        import logging

        raw_name = "-home-user-NowhereProject"
        mapping = {raw_name: "NowhereProject"}

        # canonical_dir does NOT exist in the repo
        canonical_dir = claude_env["repo"] / "claude" / "projects" / "NowhereProject"
        assert not canonical_dir.exists()

        # memory_dir is a dangling symlink
        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        memory_dir = project_dir / "memory"
        gone_target = claude_env["repo"] / "claude" / "projects" / "AlsoGone"
        memory_dir.symlink_to(gone_target)
        assert memory_dir.is_symlink()
        assert not memory_dir.exists()  # dangling

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)

        target = make_copy_target(claude_env)
        with caplog.at_level(logging.WARNING, logger="mesy"):
            target.scan()

        # memory_dir must be a real (empty) directory — not a symlink
        assert not memory_dir.is_symlink()
        assert memory_dir.is_dir()
        assert list(memory_dir.iterdir()) == []
        assert any("No source data found" in r.message for r in caplog.records)


class TestGetStageDirs:
    def test_returns_correct_dirs(self, claude_env):
        """get_stage_dirs() returns ['global/', 'projects/']."""
        target = make_target(claude_env)
        assert target.get_stage_dirs() == ["claude/global/", "claude/projects/"]
