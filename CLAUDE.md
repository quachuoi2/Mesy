# Mesy

Git-backed daemon syncing AI tool memory files across devices via GitHub. Setup, config, sync modes and conflict rules live in [README.md](README.md) — do not restate them here.

## Commands

```bash
source .venv/bin/activate   # REQUIRED before any python command
python -m pytest tests/     # never bare `pytest`: it may be a system install without project deps
python sync.py              # run daemon manually
```

## Architecture

- `SyncTarget` (`src/targets/base.py`) owns file/folder sync (`_sync_file`, `_sync_dir`) for both modes; child targets only choose which local/repo pairs to sync
- `src/factory.py` is the only module that knows concrete target classes

## Rules

- Pure Python, minimal deps (only `watchdog`, optional)
- All git operations via `subprocess`, not GitPython
- Offline must not crash: local commits accumulate until push succeeds
- Must work on Linux and Windows (`Path` objects)
- Misconfiguration exits non-zero — never `return`, or `Restart=on-failure` leaves the daemon dead
- Timing intervals must be finite and positive (`nan` stalls the scan forever)
- Tests mock git and filesystem — never actually commit/push
