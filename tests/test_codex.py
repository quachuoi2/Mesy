"""Codex adoption and export tests use isolated filesystems, never live homes."""

from configparser import ConfigParser
from pathlib import Path

import pytest

from src.config import load_targets
from src.factory import RepoContext, build_targets, validate_ownership
from src.links import LinkConflict, inventory
from src.watch import _event_relevant, _snapshot
from src.targets.codex import CodexTarget


@pytest.fixture
def target(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    return CodexTarget("codex", tmp_path / "home" / ".codex", data / "codex", data,
                       skills_source=tmp_path / "home" / ".agents" / "skills")


def test_setup_bootstraps_memory_without_touching_native_state(target):
    target.source.mkdir(parents=True)
    (target.source / "auth.json").write_text("private")
    (target.source / "memories_1.sqlite").write_bytes(b"database")
    target.scan()
    assert (target.source / "AGENTS.md").is_symlink()
    assert "mesy-memory/INDEX.md" in (target.source / "AGENTS.md").read_text()
    assert (target.source / "mesy-memory" / "INDEX.md").is_file()
    assert not (target.repo_dir / "auth.json").exists()
    assert not (target.repo_dir / "memories_1.sqlite").exists()
    assert (target.source / "auth.json").read_text() == "private"


def test_idempotent_setup_preserves_mtime_and_no_extra_backups(target):
    target.scan()
    before = inventory(target.repo_dir)
    mtime = (target.repo_dir / "global" / "AGENTS.md").stat().st_mtime_ns
    target.scan()
    assert inventory(target.repo_dir) == before
    assert (target.repo_dir / "global" / "AGENTS.md").stat().st_mtime_ns == mtime


def test_existing_instructions_backed_up_and_preserved(target):
    target.source.mkdir(parents=True)
    (target.source / "AGENTS.md").write_text("Personal instructions\n")
    target.scan()
    assert (target.source / "AGENTS.md").read_text().startswith("Personal instructions")
    backups = list((target.state_dir / "backups").glob("*/content"))
    assert any(p.is_file() and p.read_text() == "Personal instructions\n" for p in backups)


def test_existing_different_instructions_are_not_merged(target):
    target.source.mkdir(parents=True)
    local = target.source / "AGENTS.md"
    local.write_text("Local policy")
    remote = target.repo_dir / "global" / "AGENTS.md"
    remote.parent.mkdir(parents=True)
    remote.write_text("Remote policy")
    with pytest.raises(LinkConflict):
        target.scan()
    assert local.read_text() == "Local policy"
    assert remote.read_text() == "Remote policy"
    assert not local.is_symlink()
    assert list((target.state_dir / "conflicts").glob("*/local"))


def test_atomic_save_relinks_when_repository_unchanged(target):
    target.scan()
    path = target.source / "AGENTS.md"
    saved = path.with_name("saved.md")
    saved.write_text(path.read_text() + "\nNew instruction\n")
    saved.replace(path)
    assert not path.is_symlink()
    target.scan()
    assert path.is_symlink()
    assert "New instruction" in path.read_text()


def test_atomic_save_and_repository_edit_preserve_both(target):
    target.scan()
    target.scan()
    path = target.source / "AGENTS.md"
    saved = path.with_name("saved.md")
    saved.write_text("Local change")
    saved.replace(path)
    (target.repo_dir / "global" / "AGENTS.md").write_text("Remote change")
    with pytest.raises(LinkConflict):
        target.scan()
    assert path.read_text() == "Local change"
    assert (target.repo_dir / "global" / "AGENTS.md").read_text() == "Remote change"


def test_directory_adoption_preserves_nested_assets_and_modes(target):
    target.skills = ["mine"]
    skill = target.skills_source / "mine"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("Skill")
    script = skill / "scripts" / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    target.scan()
    assert skill.is_symlink()
    assert (skill / "scripts" / "run.sh").stat().st_mode & 0o111
    assert (target.repo_dir / "skills" / "mine" / "scripts" / "run.sh").read_text() == script.read_text()


def test_directory_atomic_save_changes_repository_immediately(target):
    target.scan()
    local = target.source / "mesy-memory" / "notes.md"
    local.write_text("old")
    temporary = local.with_name("tmp.md")
    temporary.write_text("new")
    temporary.replace(local)
    assert (target.repo_dir / "shared" / "notes.md").read_text() == "new"
    local.unlink()
    assert not (target.repo_dir / "shared" / "notes.md").exists()


def test_deleted_link_repaired_and_detach_retains_latest(target):
    target.scan()
    link = target.source / "mesy-memory"
    link.unlink()
    target.scan()
    assert link.is_symlink()
    (link / "note.md").write_text("Latest")
    target.detach()
    assert not link.is_symlink()
    assert (link / "note.md").read_text() == "Latest"
    assert not (target.source / "AGENTS.md").is_symlink()


def test_failed_symlink_probe_never_removes_source(target, monkeypatch):
    target.source.mkdir(parents=True)
    path = target.source / "AGENTS.md"
    path.write_text("Keep")
    def fail(*args, **kwargs):
        raise OSError("symlink unavailable")
    monkeypatch.setattr(Path, "symlink_to", fail)
    with pytest.raises(OSError, match="symlink unavailable"):
        target.scan()
    assert path.read_text() == "Keep"
    assert not path.is_symlink()


def test_nested_symlinks_rejected_without_deleting_directory(target, tmp_path):
    path = target.source / "mesy-memory"
    path.mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("private")
    (path / "nested.md").symlink_to(secret)
    with pytest.raises(ValueError, match="Nested symlinks"):
        target.scan()
    assert not path.is_symlink()
    assert secret.read_text() == "private"


def test_wrong_link_preserves_previous_target(target, tmp_path):
    target.source.mkdir(parents=True)
    original = tmp_path / "old.md"
    original.write_text("Old instructions")
    (target.source / "AGENTS.md").symlink_to(original)
    target.scan()
    assert original.read_text() == "Old instructions"
    assert (target.source / "AGENTS.md").resolve() == target.repo_dir / "global" / "AGENTS.md"


def test_preview_makes_no_files(target):
    before = sorted(target.data_dir.rglob("*"))
    assert "AGENTS.md" in "\n".join(target.preview())
    assert sorted(target.data_dir.rglob("*")) == before
    assert not target.source.exists()


def test_override_warned_not_adopted(target, caplog):
    target.source.mkdir(parents=True)
    override = target.source / "AGENTS.override.md"
    override.write_text("Local override")
    target.scan()
    assert "shadows" in caplog.text
    assert not override.is_symlink()


def test_exports_require_two_stable_observations_and_preserve_native(target):
    native = target.source / "memories"
    native.mkdir(parents=True)
    (native / "summary.md").write_text("Remember")
    (native / "extensions").mkdir()
    (native / "extensions" / "private.md").write_text("Computer history")
    (native / "data.sqlite").write_bytes(b"private")
    target.export_memories = True
    target.export_settle_seconds = 0
    target.device_id = "device-a"
    target.scan()
    assert not (target.repo_dir / "generated" / "device-a").exists()
    target.scan()
    snapshots = list((target.repo_dir / "generated" / "device-a" / "snapshots").iterdir())
    assert len(snapshots) == 1
    assert (snapshots[0] / "summary.md").read_text() == "Remember"
    assert not (snapshots[0] / "extensions").exists()
    assert not (snapshots[0] / "data.sqlite").exists()
    assert not native.is_symlink()
    index = target.repo_dir / "generated" / "device-a" / "INDEX.md"
    previous = index.stat().st_mtime_ns
    target.scan()
    assert index.stat().st_mtime_ns == previous


def test_changed_export_waits_and_retains_old_snapshots(target):
    native = target.source / "memories"
    native.mkdir(parents=True)
    file = native / "MEMORY.md"
    file.write_text("One")
    target.export_memories = True
    target.export_settle_seconds = 0
    target.device_id = "device-b"
    target.scan()
    target.scan()
    snapshots = target.repo_dir / "generated" / "device-b" / "snapshots"
    file.write_text("Two")
    target.scan()
    assert len(list(snapshots.iterdir())) == 1
    target.scan()
    assert len(list(snapshots.iterdir())) == 2


def test_device_id_persists_and_other_devices_are_not_written(target):
    first = target._device()
    assert target._device() == first
    other = target.repo_dir / "generated" / "other" / "note.md"
    other.parent.mkdir(parents=True)
    other.write_text("Other device")
    target.scan()
    assert other.read_text() == "Other device"


def test_config_explicit_type_and_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-home"))
    config = ConfigParser()
    config.read_string("[target:codex]\ntype = codex\nrepo_subdir = codex\nskills = one, two\n")
    definition = load_targets(config)[0]
    assert definition["source"] == tmp_path / "custom-home"
    assert definition["skills"] == ["one", "two"]
    assert definition["export_memories"] is False
    assert isinstance(build_targets([definition], tmp_path)[0], CodexTarget)


@pytest.mark.parametrize("subdir", ["../escape", "/outside", "."])
def test_reject_unsafe_destination(tmp_path, subdir):
    with pytest.raises(ValueError):
        build_targets([{"name": "codex", "type": "codex", "source": tmp_path / "home", "repo_subdir": subdir}], tmp_path)


def test_duplicate_sources_rejected(target):
    duplicate = CodexTarget("other", target.source, target.data_dir / "other", target.data_dir)
    context = RepoContext("repo", target.data_dir, [target, duplicate])
    with pytest.raises(ValueError, match="Overlapping sources"):
        validate_ownership([context])


def test_destination_symlink_rejected(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "codex").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError):
        CodexTarget("codex", tmp_path / "home", data / "codex", data)


def test_event_and_polling_cover_scripts_not_credentials(target):
    target.scan()
    context = RepoContext("repo", target.data_dir, [target], target.get_stage_dirs())
    assert _event_relevant(context, str(target.source / "AGENTS.md"))
    assert not _event_relevant(context, str(target.source / "auth.json"))
    assert not _event_relevant(context, str(target.state_dir / "backups" / "file"))
    before = _snapshot(context)
    (target.repo_dir / "shared" / "helper.py").write_text("print('ok')")
    assert _snapshot(context) != before


def test_detach_failed_copy_leaves_link(target, monkeypatch):
    target.scan()
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("src.links.copy_path", fail)
    with pytest.raises(OSError, match="disk full"):
        target.detach()
    assert (target.source / "AGENTS.md").is_symlink()


def test_same_content_with_different_read_permissions_can_be_adopted(target):
    target.source.mkdir(parents=True)
    local = target.source / "AGENTS.md"
    local.write_text("same")
    local.chmod(0o600)
    repo = target.repo_dir / "global" / "AGENTS.md"
    repo.parent.mkdir(parents=True)
    repo.write_text("same")
    repo.chmod(0o644)
    target.scan()
    assert local.is_symlink()


def test_failed_final_link_creation_restores_original(target, monkeypatch):
    target.source.mkdir(parents=True)
    source = target.source / "AGENTS.md"
    source.write_text("keep")
    original = Path.symlink_to
    def fail_final(self, destination, **kwargs):
        if self == source:
            raise OSError("final link failure")
        return original(self, destination, **kwargs)
    monkeypatch.setattr(Path, "symlink_to", fail_final)
    with pytest.raises(OSError, match="final link failure"):
        target.scan()
    assert source.read_text() == "keep"
    assert not source.is_symlink()


def test_unstable_native_read_never_publishes(target, monkeypatch):
    target.export_memories = True
    target.export_settle_seconds = 0
    values = iter([{"note.md": b"one"}, {"note.md": b"one"}, {"note.md": b"two"}])
    monkeypatch.setattr(target, "_read_native", lambda: next(values))
    target.scan()
    target.scan()
    assert not list((target.repo_dir / "generated").rglob("manifest.json"))
