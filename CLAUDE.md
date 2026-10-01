# Mesy

Git-backed daemon syncing AI tool memory files across devices via GitHub. Setup, config, sync modes and conflict rules live in [README.md](README.md) — do not restate them here.

## Commands

```bash
source .venv/bin/activate   # REQUIRED before any python command
python -m pytest tests/     # never bare `pytest`: it may be a system install without project deps
python sync.py              # run daemon manually
```

## Architecture

- Sync modes (`src/sync_modes/`: `SymlinkMode`, `CopyMode`) own file/folder sync (`sync_file`, `sync_dir`), including link repair; targets only choose which local/repo pairs to sync and hand them to `self.mode`
- `src/factory.py` is the only module that knows concrete target and mode classes; it builds one set of modes per data repo, so copy-mode targets share one hash file

## Rules

- Pure Python, minimal deps (only `watchdog`, optional)
- All git operations via `subprocess`, not GitPython
- Offline must not crash: local commits accumulate until push succeeds
- Must work on Linux and Windows (`Path` objects)
- Misconfiguration exits non-zero — never `return`, or `Restart=on-failure` leaves the daemon dead
- Timing intervals must be finite and positive (`nan` stalls the scan forever)
- Tests mock git and filesystem — never actually commit/push
