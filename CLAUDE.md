# Mesy

Git-backed daemon syncing AI tool memory files across devices via GitHub.

## Quick Reference

| Item | Value |
|------|-------|
| Language | Python 3 |
| Venv | `.venv/` (always activate before running anything) |
| Entry point | `sync.py` → `src.main` |
| Repo list | `repos.conf` at repo root — **required**, lists all data repos |
| Per-repo config | `sync.conf` inside each data repo |
| Daemon | systemd user service (`mesy.service`) |

## Commands

```bash
# Activate venv (REQUIRED before pytest or any python command)
source .venv/bin/activate

# Run tests
python -m pytest tests/ -v          # full suite
python -m pytest tests/ -x          # stop on first failure
python -m pytest tests/test_git_utils.py  # single module
python -m pytest -k "test_name"     # by name pattern

# Run daemon manually
python sync.py
```

**Important:** Do NOT use bare `pytest` — use `python -m pytest` to ensure the venv's pytest is used. `which pytest` may resolve to a system-level pytest that lacks project dependencies.

## Project Structure

Canonical module map. Self-explanatory files (`config.py`, `base.py`, most tests) omitted — the names cover them.

```
src/
├── main.py           # Daemon loop (watchdog or polling), multi-repo
├── git_utils.py      # LEGACY transport — merges in the live worktree
├── transport.py      # CODEX transport — merges in a detached worktree
├── links.py          # Verified symlink adoption, backups, conflict capture
├── sync_state.py     # Content hashes for copy-mode three-way sync
├── pending.py        # Unmapped Claude Code projects
└── targets/
    ├── claude_code.py   # Maps per-device project names to canonical ones
    ├── codex.py         # Symlink only, optional memory exports
    ├── generic.py       # Any flat .md directory
    └── _migration.py    # Initial file migration helpers

tests/
├── test_transport.py              # Transport decisions, git mocked
└── test_transport_integration.py  # Transport against real local git repos
```

Two transports coexist: a repo containing a Codex target uses `transport.py` for **all** its targets.

## Architecture

- **REPO_DIR** = this repo (Mesy code); `repos.conf` lives here at the root
- **repos.conf** = list of `[repo:<name>]` sections, each with a `path = <data_repo>` key. **Required** — the daemon exits if it is missing or empty
- Each data repo is an independent git repo (e.g. AlfredMemory) containing its own `sync.conf`
- `sync.conf` defines `[mesy]` timing settings and `[target:*]` sections; each target syncs files between a `source` (tool's directory) and `repo_dir` (subdir in the data repo)

Sync modes, three-way copy rules, and the conflict-policy table live in [README.md](README.md) — do not restate them here. Codex specifics are in [docs/CODEX_SYNC.md](docs/CODEX_SYNC.md).

**Before touching conflict code:** there are two policies, not one. Copy-mode merges both versions into a single file; Codex adoption and Codex-repo git merges keep versions as separate copies and never write markers into live files. See the README table.

### Failure behavior

- A failed scan blocks commits for the ENTIRE repo, not just the failing target
- Codex repos also stop pulling; legacy repos keep pulling (one-way sync until fixed)
- Scan failures log once at ERROR until the message changes; recovery logs at INFO
- Misconfiguration exits non-zero — never `return`, or `Restart=on-failure` leaves the daemon dead
- Timing intervals must be finite and positive (`nan` would stall the scan forever)

## Coding Rules

- Pure Python, minimal deps (only `watchdog` optional)
- All git operations via `subprocess` (not GitPython)
- Handle network failures gracefully (offline = accumulate local commits)
- Must work on Linux and Windows (use `Path` objects)
- Mock git and filesystem in tests — never actually commit/push

## Testing Notes

- All fixtures in `conftest.py` — `tmp_repo` patches `REPO_DIR` and `REPOS_CONF_PATH` and returns a tmp data repo dir; `tmp_multi_repo` sets up two independent data repos for multi-repo tests
- `isolated_sync_state` fixture (autouse) ensures hash state isolation between tests
- `load_config(data_dir)` and `load_project_mappings(data_dir)` now require an explicit `data_dir`; pass the `tmp_repo` fixture value from tests
- Test conflict resolution with prepared conflict files
- Use `tmp_path` for filesystem tests
