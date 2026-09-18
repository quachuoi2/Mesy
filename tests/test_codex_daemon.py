"""Daemon and setup integration for Codex targets."""

from configparser import ConfigParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import setup
from src.config import RepoContext, load_targets
from src.main import _commit_context, _pull_context, _scan_context, run_with_watchdog
from src.targets.codex import CodexTarget


@pytest.fixture
def context(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    target = CodexTarget("codex", tmp_path / "home", data / "codex", data)
    target.scan()
    return RepoContext("data", data, [target], target.get_stage_dirs(), {
        "debounce_seconds": "1", "scan_interval_seconds": "1", "pull_interval_seconds": "1",
    })


def test_codex_uses_safe_transport_not_legacy_git(context, monkeypatch):
    transport = MagicMock(return_value=True)
    monkeypatch.setattr("src.main.sync_repository", transport)
    legacy = MagicMock()
    monkeypatch.setattr("src.main.commit_and_push", legacy)
    _commit_context(context)
    transport.assert_called_once()
    legacy.assert_not_called()


def test_periodic_pull_retries_without_new_files(context, monkeypatch):
    transport = MagicMock(return_value=True)
    monkeypatch.setattr("src.main.sync_repository", transport)
    _pull_context(context)
    transport.assert_called_once()


def test_scan_error_does_not_stop_other_targets(context):
    first = MagicMock(name="broken")
    first.scan.side_effect = OSError("disk full")
    second = MagicMock(name="healthy")
    context.targets = [first, second]
    assert not _scan_context(context)
    second.scan.assert_called_once()


def test_watchdog_ignores_reads_and_accepts_atomic_moves(context, monkeypatch):
    pytest.importorskip("watchdog")
    observer = MagicMock()
    monkeypatch.setattr("src.main.Observer", lambda: observer)
    def stop(_):
        raise KeyboardInterrupt
    monkeypatch.setattr("src.main.time.sleep", stop)
    run_with_watchdog([context], context.settings)
    handler = observer.schedule.call_args_list[0].args[0]
    # A read should not call time.time() or trigger a sync.
    clock = MagicMock(return_value=100)
    monkeypatch.setattr("src.main.time.time", clock)
    handler.on_any_event(SimpleNamespace(event_type="opened", src_path=str(context.data_dir / "codex/global/AGENTS.md")))
    clock.assert_not_called()
    handler.on_any_event(SimpleNamespace(event_type="moved", src_path=str(context.targets[0].source / "tmp"), dest_path=str(context.targets[0].source / "AGENTS.md")))
    clock.assert_called_once()
    observer.stop.assert_called_once()
    observer.join.assert_called_once()


def test_codex_setup_preview_is_read_only(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    home = tmp_path / "home"
    (data / "sync.conf").write_text(f"[target:codex]\ntype = codex\nsource = {home}\nrepo_subdir = codex\n")
    setup.configured_codex("preview", data)
    assert "AGENTS.md" in capsys.readouterr().out
    assert not home.exists()
    assert list(data.iterdir()) == [data / "sync.conf"]


def test_setup_and_daemon_share_target_parser(tmp_path):
    (tmp_path / "sync.conf").write_text("[target:codex]\ntype = codex\nrepo_subdir = codex\n")
    definitions = setup.load_sync_conf_targets(tmp_path)
    assert definitions[0]["type"] == "codex"
    assert definitions[0]["sync_mode"] == "symlink"


def test_multiple_generic_sources_are_not_skipped(tmp_path, monkeypatch):
    (tmp_path / "sync.conf").write_text(
        f"[target:a]\nsource = {tmp_path / 'one'}\nrepo_subdir = a\n"
        f"[target:b]\nsource = {tmp_path / 'two'}\nrepo_subdir = b\n")
    adopt = MagicMock()
    monkeypatch.setattr(setup, "setup_generic_target", adopt)
    setup.setup_repo(tmp_path, set())
    assert adopt.call_count == 2


def test_codex_copy_mode_rejected():
    config = ConfigParser()
    config.read_string("[target:codex]\ntype=codex\nrepo_subdir=codex\nsync_mode=copy\n")
    with pytest.raises(ValueError, match="symlink"):
        load_targets(config)
