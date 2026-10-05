# Mesy — AI Memory Sync

Git-backed daemon that syncs AI tool memory files across your devices. One continuous memory, everywhere.

## How It Works

Mesy is **code only**. Your memory lives in one or more separate **data repos** (private git repos you own). The daemon watches each data repo's targets, auto-commits changes, and pushes/pulls via GitHub.

```
Device A                          GitHub (private)            Device B
~/.claude/projects/ ──symlink──┐                        ┌──symlink── ~/.claude/projects/
~/.openclaw/workspace/ ─copy──┐│                        │┌──copy──── ~/.openclaw/workspace/
                              vv                        vv
            ~/Workspace/AI/Memory/AlfredMemory <──push/pull──> ~/Workspace/AI/Memory/AlfredMemory
                                    ^
                                    │ managed by
                              ~/Workspace/AI/Memory/Mesy (this repo)
```

## Layout

```
Mesy/                          # This repo — code only, no memory
├── sync.py                    # Entry point — run this
├── setup.py                   # Interactive onboarding (Linux/Windows/WSL)
├── repos.ini                 # REQUIRED — list of data repos (gitignored)
├── mesy.service               # Systemd unit template
├── src/                       # Daemon code
├── tests/                     # pytest suite
└── docs/                      # Design notes, dual-boot setup
```

A data repo (e.g. `AlfredMemory`) looks like:

```
AlfredMemory/
├── sync.ini                  # Timing + [target:*] definitions
├── claude/
│   ├── global/CLAUDE.md       # Linked to ~/.claude/CLAUDE.md
│   ├── global/rules/          # Linked to ~/.claude/rules/
│   ├── projects.ini          # Canonical project name mappings
│   └── projects/              # Synced Claude Code memory
│       └── .pending           # Unmapped projects awaiting a mapping
└── openclaw/main/             # Synced OpenClaw memory
```

## Setup

### First device

1. Create a private GitHub repo for your memory (the data repo).
2. Clone Mesy and run setup:
   ```bash
   git clone git@github.com:<you>/Mesy.git ~/Workspace/AI/Memory/Mesy
   cd ~/Workspace/AI/Memory/Mesy
   python3 setup.py
   ```

`setup.py` prompts for each data repo's name, local path, and git remote, writes `repos.ini`, offers a template `sync.ini`, then migrates existing memory and installs the daemon.

3. Edit the data repo's `sync.ini` targets and `claude/projects.ini` mappings, then restart the daemon.

### Every additional device

Same commands. Point setup at the same data repo URL; it clones and symlinks in place. Add this device's raw project names to `claude/projects.ini` (see below).

## Configuration

### `repos.ini` (Mesy root, required)

Daemon exits if this is missing or empty.

```ini
[repo:alfred-memory]
path = /home/you/Workspace/AI/Memory/AlfredMemory

[repo:dmserver-notes]
path = /home/you/Workspace/Note/DMServer
```

### `sync.ini` (inside each data repo)

```ini
[mesy]
debounce_seconds = 600       # Wait after last change before pushing
pull_interval_seconds = 600  # How often to pull remote changes
scan_interval_seconds = 600  # How often to scan for new projects

[target:claude-code]
source = ~/.claude/projects
repo_subdir = claude/projects

[target:openclaw]
source = ~/.openclaw/workspace
repo_subdir = openclaw/main
file_patterns = *.md
exclude = TOOLS.md, HEARTBEAT.md, .openclaw/
sync_mode = copy

[target:codex]
repo_subdir = codex
```

The section name sets the target type: `[target:claude-code]`, `[target:codex]`, or any other name for a generic target.

The Codex target syncs `~/.codex/AGENTS.md` with `codex/AGENTS.md` and the whole `~/.codex/memories/` folder with `codex/memories/`. Project `AGENTS.md` files stay in their own repositories. Its `source` defaults to `$CODEX_HOME`, else `~/.codex`.

Defaults if omitted: `debounce_seconds = 30`, both intervals `60`, `file_patterns = *.md`, `sync_mode = symlink`. Polling mode (no `watchdog`) ticks at the smallest `debounce_seconds` across repos, floored at 60s.

All three intervals must be finite positive numbers. A missing, zero, negative, non-numeric, `nan`, or `inf` value, or a `sync_mode` other than `symlink` or `copy`, is a configuration error: the daemon logs it and exits non-zero rather than starting in a broken state.

## Sync Modes

| Mode | Mechanism | Use case |
|------|-----------|----------|
| `symlink` | Source files replaced with symlinks into the repo | Claude Code |
| `copy` | Bidirectional copy with three-way hash tracking | OpenClaw (no symlink support) |

Copy mode stores a content hash and the text of each file after each sync (`src/sync_state.py`) and compares both sides against it:

| Source | Repo | Result |
|--------|------|--------|
| changed | unchanged | source wins |
| unchanged | changed | repo wins |
| changed | changed | merged against the stored text (see Conflicts) |
| unchanged | unchanged | no action |

With no stored hash yet, files that differ go to the source side's version, and identical files get their hash stored so the next one-sided edit is copied, not overwritten.

## Conflicts

Nothing is lost in a conflict. Edits that don't overlap are merged; overlapping lines get git's own `<<<<<<<` / `=======` / `>>>>>>>` markers, and the file is written to both sides. VS Code shows Accept buttons above each marked block.

| Where | Merge | Labels |
|---|---|---|
| Git rebase | git's, left as is | `HEAD` = the other machine; the other label names this machine's commit (`sync from <host> at <time>`) |
| Copy mode, both sides changed | `git merge-file` against the stored text | `local (<host>)` / `repo` |
| Migration (a real local file meets a different repo copy) | `git merge-file` with no base: every differing block is marked | `local (<host>)` / `repo` |

## Project Name Mapping (Claude Code)

Claude Code encodes the working directory into the memory folder name (`/home/you/Workspace` → `-home-you-Workspace`), so the same project has a different name on each device. `<data_repo>/claude/projects.ini` maps them to one canonical name:

```ini
[Workspace]
paths = -home-you-Workspace, -C-Users-you-Workspace, -mnt-wsl-...-Workspace
```

Unmapped projects are recorded in `<data_repo>/claude/projects/.pending` — a single shared file listing each raw name with a ready-to-paste config snippet. Nothing else is written until you add the mapping; the next scan adopts the project and prunes the entry.

## When a Target Fails

A failed scan (usually an unresolved conflict) **blocks commits for every target in that data repo**, not just the failing one — it avoids committing a half-synced state. Pulls continue, so the repo syncs one-way until you fix it.

Logged once at `ERROR` until the message changes, with an `INFO` line on recovery. Resolve the conflict and wait for the next scan — no restart needed.

## Managing the Daemon

### Linux (systemd)

```bash
systemctl --user status mesy
systemctl --user restart mesy
tail -f log.log   # in the Mesy folder
systemctl --user stop mesy
```

Setup enables lingering so the daemon survives logout.

### Windows (Task Scheduler)

Registered via PowerShell's `Register-ScheduledTask` (an ONLOGON trigger through `schtasks /Create` would need admin).

```powershell
schtasks /Query /TN Mesy
schtasks /Run /TN Mesy
schtasks /End /TN Mesy
```

## Testing

```bash
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -v
```

Use `python -m pytest`, not bare `pytest` — the latter may resolve to a system install.

## Dependencies

- **git** — required
- **Python 3** — required
- **watchdog** (`pip install watchdog`) — optional, falls back to polling

## Dual-Boot / WSL

See [docs/DUAL_BOOT_SETUP.md](docs/DUAL_BOOT_SETUP.md) for path matching and systemd considerations.

## Future: Alfred Integration

- **Phase 2**: Alfred gains a `MemoryManager` reading/writing the data repos directly
- **Phase 4**: The daemon becomes an Alfred module; clients push memory via Alfred's API instead of git
