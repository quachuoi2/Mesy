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

```
Mesy/
├── sync.py                    # Entry point (delegates to src.main)
├── setup.py                   # One-time onboarding (symlinks, daemon install)
├── mesy.service               # systemd unit file
├── src/
│   ├── main.py                # Daemon loop (watchdog or polling), multi-repo
│   ├── config.py              # repos.conf + per-repo sync.conf loading
│   ├── git_utils.py           # Git operations, conflict resolution
│   ├── sync_state.py          # Content hash tracking for three-way sync
│   └── targets/
│       ├── base.py            # SyncTarget ABC
│       ├── claude_code.py     # Claude Code target (symlink mode)
│       └── generic.py         # Generic target (symlink or copy mode)
├── tests/
│   ├── conftest.py            # Shared fixtures (tmp_repo, monkeypatching)
│   ├── test_config.py
│   ├── test_git_utils.py
│   ├── test_main.py
│   ├── test_sync_state.py
│   └── test_targets/
│       ├── conftest.py
│       ├── test_base.py
│       ├── test_claude_code.py
│       └── test_generic.py
```

## Architecture

- **REPO_DIR** = this repo (Mesy code); `repos.conf` lives here at the root
- **repos.conf** = list of `[repo:<name>]` sections, each with a `path = <data_repo>` key. **Required** — the daemon exits if it is missing or empty
- Each data repo is an independent git repo (e.g. AlfredMemory) containing its own `sync.conf`
- `sync.conf` defines `[mesy]` timing settings and `[target:*]` sections; each target syncs files between a `source` (tool's directory) and `repo_dir` (subdir in the data repo)

### Sync modes

| Mode | Mechanism | Use case |
|------|-----------|----------|
| `symlink` | Source files replaced with symlinks into repo | Claude Code (tool supports symlinks) |
| `copy` | Bidirectional copy with three-way hash tracking | OpenClaw (can't use symlinks) |

### Three-way sync (copy mode)

Uses `src/sync_state.py` to store content hashes after each successful sync. On next scan, compares source and repo against the stored base hash:

- Source changed, repo didn't → source wins
- Repo changed, source didn't → repo wins
- Both changed → real conflict (merge both, set `conflict: true`)
- Neither changed → no action

### Conflict resolution

- Conflicted files get both versions wrapped in `<!-- CONFLICT -->` HTML comments
- Frontmatter gets `conflict: true` flag
- `merge_conflicting_file()` writes to BOTH local and repo files

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
