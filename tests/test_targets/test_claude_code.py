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



class TestSyncClaudeMd:
    def test_copies_and_symlinks_project_claude_md(self, claude_env, monkeypatch):
        """_sync_file() copies project CLAUDE.md to repo and symlinks it (symlink mode)."""
        raw_name = "-home-user-Project"
        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        local_claude = project_dir / "CLAUDE.md"
        local_claude.write_text("# Project Config")

        repo_dir = claude_env["repo"] / "claude" / "projects" / "Project"
        repo_dir.mkdir(parents=True)

        target = make_target(claude_env)
        repo_claude = repo_dir / "CLAUDE.md"
        target._sync_file(local_claude, repo_claude, label=f"CLAUDE.md for {raw_name}")

        assert repo_claude.exists()
        assert local_claude.is_symlink()
        assert local_claude.resolve() == repo_claude.resolve()

    def test_skips_if_already_symlinked(self, claude_env):
        """_sync_file() does nothing if CLAUDE.md is already a symlink (symlink mode)."""
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
        target._sync_file(local_claude, repo_claude, label=f"CLAUDE.md for {raw_name}")

        # Symlink should still resolve to the same place
        assert local_claude.is_symlink()
        assert local_claude.resolve() == repo_claude.resolve()


class TestSafetyVerification:
    def test_rmtree_skipped_on_failed_migration(self, claude_env, monkeypatch, caplog):
        """rmtree is NOT called when _verify_migration returns False."""
        import logging

        raw_name = "-home-user-BrokenProject"
        mapping = {raw_name: "BrokenProject"}

        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "important.md").write_text("critical data")

        # Force _verify_migration to return False so the safety gate fires.
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: mapping)
        target = make_target(claude_env)
        monkeypatch.setattr(target, "_verify_migration", lambda src, dst: False)

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
        sync_mode="copy",
    )


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

        scratch_dest = claude_env["scratch_dir"] / raw_name
        scratch_dest.mkdir(parents=True)
        (scratch_dest / "data.md").write_text("# Scratch data")

        project_dir = claude_env["projects_dir"] / raw_name
        project_dir.mkdir(parents=True)
        memory_dir = project_dir / "memory"
        memory_dir.symlink_to(scratch_dest)
        assert memory_dir.is_symlink()

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

        target = make_copy_target(claude_env)
        target.scan()

        # Unmapped symlinked memory_dir must remain as-is
        assert memory_dir.is_symlink()
        assert memory_dir.resolve() == scratch_dest.resolve()


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
        assert any(
            "Symlink target missing" in r.message or "symlink target missing" in r.message
            for r in caplog.records
        )

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
        assert any(
            "No source data found" in r.message or "dangling symlink with no usable fallback" in r.message
            for r in caplog.records
        )


class TestUnmappedMarker:
    def test_unmapped_creates_local_needs_mapping_marker(
        self, claude_env, monkeypatch
    ) -> None:
        """Unmapped project: .needs_mapping written inside local project dir.

        No writes to the repo (no .pending/ subtree, no canonical dir).
        """
        raw_name = "-home-user-UnknownProject"
        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "data.md").write_text("some data")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

        target = make_target(claude_env)
        target.scan()

        # Marker written locally inside the project dir
        marker = project_dir / ".needs_mapping"
        assert marker.exists(), "Expected .needs_mapping inside local project dir"
        assert raw_name in marker.read_text()

        # Zero repo writes — no .pending/, no canonical dir
        repo_projects = claude_env["repo"] / "claude" / "projects"
        assert not (repo_projects / ".pending").exists()
        assert not (repo_projects / raw_name).exists()
        # memory_dir must NOT become a symlink
        assert not memory_dir.is_symlink()

    def test_unmapped_copy_mode_same_behavior(self, claude_env, monkeypatch) -> None:
        """Unmapped project in copy mode: same local-only marker, no repo writes."""
        raw_name = "-home-user-CopyUnknown"
        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "file.md").write_text("content")

        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})

        target = make_copy_target(claude_env)
        target.scan()

        marker = project_dir / ".needs_mapping"
        assert marker.exists()
        assert raw_name in marker.read_text()

        repo_projects = claude_env["repo"] / "claude" / "projects"
        assert not (repo_projects / ".pending").exists()
        assert not (repo_projects / raw_name).exists()
        assert not memory_dir.is_symlink()


class TestPhase1RepoRename:
    def test_renames_raw_dir_in_repo_to_canonical(self, claude_env, monkeypatch) -> None:
        """scan() renames a raw-named repo dir to its canonical name via Phase 1."""
        raw_name = "-home-user-OldProject"
        mapping = {raw_name: "OldProject"}

        projects_dir = claude_env["repo"] / "claude" / "projects"
        raw_dir = projects_dir / raw_name
        raw_dir.mkdir(parents=True)
        (raw_dir / "notes.md").write_text("# Old Notes")

        monkeypatch.setattr(
            "src.targets.claude_code.load_project_mappings", lambda **kw: mapping
        )

        target = make_target(claude_env)
        target.scan()

        canonical_dir = projects_dir / "OldProject"
        assert canonical_dir.exists()
        assert (canonical_dir / "notes.md").exists()
        assert not raw_dir.exists()

    def test_skips_when_canonical_already_exists(self, claude_env, monkeypatch) -> None:
        """scan() does not clobber an existing canonical dir if raw dir also exists."""
        raw_name = "-home-user-Project"
        mapping = {raw_name: "Project"}

        projects_dir = claude_env["repo"] / "claude" / "projects"
        raw_dir = projects_dir / raw_name
        raw_dir.mkdir(parents=True)
        (raw_dir / "raw.md").write_text("raw data")
        canonical_dir = projects_dir / "Project"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "existing.md").write_text("already here")

        monkeypatch.setattr(
            "src.targets.claude_code.load_project_mappings", lambda **kw: mapping
        )

        target = make_target(claude_env)
        # scan() would assert the raw dir remains if rename was skipped — but
        # because the raw name still appears in mapping, _assert_phase1_invariants
        # will raise.  Catch that so we can still assert the raw dir is intact.
        try:
            target.scan()
        except AssertionError:
            pass

        # raw_dir must not have been renamed / merged into canonical_dir
        assert raw_dir.exists(), "raw dir must be left intact to avoid data loss"
        assert (canonical_dir / "existing.md").exists()


class TestPhase1Invariants:
    def test_raises_when_raw_name_remains_after_phase1(
        self, claude_env, monkeypatch
    ) -> None:
        """_assert_phase1_invariants raises if a raw-named dir persists in the repo.

        We simulate a failed rename by monkeypatching _migrate_repo_names to be
        a no-op, then calling _assert_phase1_invariants directly.
        """
        raw_name = "-home-user-Stuck"
        mapping = {raw_name: "Stuck"}

        projects_dir = claude_env["repo"] / "claude" / "projects"
        (projects_dir / raw_name).mkdir()

        monkeypatch.setattr(
            "src.targets.claude_code.load_project_mappings", lambda **kw: mapping
        )
        target = make_target(claude_env)

        with pytest.raises(AssertionError, match="Phase 1 invariant violated"):
            target._assert_phase1_invariants(mapping)


class TestSyncFile:
    """Phase 3 _sync_file coverage in copy mode (behavioral replacement for TestSyncFileCopy)."""

    def test_copy_mode_local_to_repo_when_repo_missing(
        self, claude_env, monkeypatch
    ) -> None:
        """copy mode: local file copied to repo when repo file is absent."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        local = claude_env["claude_dir"] / "CLAUDE.md"
        local.write_text("# Local content")
        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        assert not repo.exists()

        target._sync_file(local, repo, label="test file")

        assert repo.read_text() == "# Local content"
        assert not local.is_symlink()

    def test_copy_mode_repo_to_local_when_local_missing(
        self, claude_env, monkeypatch
    ) -> None:
        """copy mode: repo file copied to local when local file is absent."""
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        repo.write_text("# Repo content")
        local = claude_env["claude_dir"] / "CLAUDE.md"
        assert not local.exists()

        target._sync_file(local, repo, label="test file")

        assert local.read_text() == "# Repo content"
        assert not local.is_symlink()

    def test_copy_mode_conflict_delegates_to_merge(
        self, claude_env, monkeypatch
    ) -> None:
        """copy mode: diverged files trigger merge_conflicting_file."""
        merge_calls: list[tuple[Path, Path]] = []

        def fake_merge(local: Path, repo: Path) -> None:
            merge_calls.append((local, repo))

        monkeypatch.setattr("src.targets.claude_code.merge_conflicting_file", fake_merge)
        monkeypatch.setattr("src.targets.claude_code.load_project_mappings", lambda **kw: {})
        target = make_copy_target(claude_env)

        local = claude_env["claude_dir"] / "CLAUDE.md"
        local.write_text("# Local version")
        repo = claude_env["repo"] / "claude" / "global" / "CLAUDE.md"
        repo.write_text("# Repo version (different)")

        target._sync_file(local, repo, label="test file")

        assert len(merge_calls) == 1
        assert merge_calls[0] == (local, repo)


class TestSyncDir:
    """Phase 3 _sync_dir coverage in copy mode — behavioral replacement for TestCopyDirFiles."""

    def test_copy_mode_dir_sync_conflict_delegates_to_merge(
        self, claude_env, monkeypatch
    ) -> None:
        """copy mode: files that differ between local and repo trigger merge."""
        merge_calls: list[tuple[Path, Path]] = []

        def fake_merge(local: Path, repo: Path) -> None:
            merge_calls.append((local, repo))

        monkeypatch.setattr("src.targets.claude_code.merge_conflicting_file", fake_merge)

        raw_name = "-home-user-ConflictProject"
        mapping = {raw_name: "ConflictProject"}

        # canonical dir with a file already in repo
        canonical_dir = claude_env["repo"] / "claude" / "projects" / "ConflictProject"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "notes.md").write_text("# Repo version")

        # local memory dir with conflicting file
        project_dir = claude_env["projects_dir"] / raw_name
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "notes.md").write_text("# Local version (different)")

        monkeypatch.setattr(
            "src.targets.claude_code.load_project_mappings", lambda **kw: mapping
        )

        target = make_copy_target(claude_env)
        target.scan()

        # merge must have been called for the conflicting file
        assert any(p.name == "notes.md" for p, _ in merge_calls), (
            "Expected merge_conflicting_file to be called for notes.md"
        )


class TestGetStageDirs:
    def test_returns_correct_dirs(self, claude_env):
        """get_stage_dirs() returns ['global/', 'projects/']."""
        target = make_target(claude_env)
        assert target.get_stage_dirs() == ["claude/global/", "claude/projects/"]
