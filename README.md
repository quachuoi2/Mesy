# Mesy — AI Memory Sync

Git-backed daemon that syncs AI tool memory files across your devices. One continuous memory, everywhere.

## How It Works

Mesy is **code only**. Your memory lives in one or more separate **data repos** (private git repos you own). The daemon watches each data repo's targets, auto-commits changes, and pushes/pulls via GitHub.

```
Device A                          GitHub (private)            Device B
~/.claude/projects/ ──symlink──┐                        ┌──symlink── ~/.claude/projects/
~/.openclaw/workspace/ ─copy──┐│                        │┌──copy──── ~/.openclaw/workspace/
                              vv                        vv
                      ~/Workspace/AlfredMemory <──push/pull──> ~/Workspace/AlfredMemory
                                    ^
                                    │ managed by
                              ~/Workspace/Mesy (this repo)
```

## Layout

```
Mesy/                          # This repo — code only, no memory
├── sync.py                    # Entry point — run this
├── setup.py                   # Interactive onboarding (Linux/Windows/WSL)
├── repos.conf                 # REQUIRED — list of data repos (gitignored)
├── mesy.service               # Systemd unit template
├── src/                       # Daemon code — see CLAUDE.md for the module map
├── tests/                     # pytest suite
└── docs/                      # Design notes, dual-boot setup
```

A data repo (e.g. `AlfredMemory`) looks like:

```
AlfredMemory/
├── sync.conf                  # Timing + [target:*] definitions
├── claude/
│   ├── global/CLAUDE.md       # Linked to ~/.claude/CLAUDE.md
│   ├── projects.conf          # Canonical project name mappings
│   └── projects/              # Synced Claude Code memory
│       └── .pending           # Unmapped projects awaiting a mapping
└── openclaw/main/             # Synced OpenClaw memory
```

## Setup

### First device

1. Create a private GitHub repo for your memory (the data repo).
2. Clone Mesy and run setup:
   ```bash
   git clone git@github.com:<you>/Mesy.git ~/Workspace/Mesy
   cd ~/Workspace/Mesy
   python3 setup.py
   ```

`setup.py` prompts for each data repo's name, local path, and git remote, writes `repos.conf`, offers a template `sync.conf`, then migrates existing memory and installs the daemon.

3. Edit the data repo's `sync.conf` targets and `claude/projects.conf` mappings, then restart the daemon.

### Every additional device

Same commands. Point setup at the same data repo URL; it clones and symlinks in place. Add this device's raw project names to `claude/projects.conf` (see below).

## Configuration

### `repos.conf` (Mesy root, required)

Daemon exits if this is missing or empty.

```ini
[repo:alfred-memory]
path = /home/you/Workspace/AlfredMemory

[repo:dmserver-notes]
path = /home/you/Workspace/Note/DMServer
```

### `sync.conf` (inside each data repo)

```ini
[mesy]
debounce_seconds = 600       # Wait after last change before pushing
pull_interval_seconds = 600  # How often to pull remote changes
scan_interval_seconds = 600  # How often to scan for new projects

[target:claude-code]
source = ~/.claude/projects
repo_subdir = claude/projects
mapping_conf = claude/projects.conf   # Presence of this key = claude-code target

[target:openclaw]
source = ~/.openclaw/workspace
repo_subdir = openclaw/main
file_patterns = *.md
exclude = TOOLS.md, HEARTBEAT.md, .openclaw/
sync_mode = copy

[target:codex]
type = codex                          # source defaults to $CODEX_HOME, else ~/.codex
repo_subdir = codex
```

The Codex target syncs `~/.codex/AGENTS.md` with `codex/AGENTS.md` and the whole `~/.codex/memories/` folder with `codex/memories/`. Project `AGENTS.md` files stay in their own repositories.

Defaults if omitted: `debounce_seconds = 30`, both intervals `60`, `file_patterns = *.md`, `sync_mode = symlink`. Polling mode (no `watchdog`) ticks at the smallest `debounce_seconds` across repos, floored at 60s.

All three intervals must be finite positive numbers. A missing, zero, negative, non-numeric, `nan`, or `inf` value is a configuration error: the daemon logs it and exits non-zero rather than starting in a broken state.

## Sync Modes

| Mode | Mechanism | Use case |
|------|-----------|----------|
| `symlink` | Source files replaced with symlinks into the repo | Claude Code |
| `copy` | Bidirectional copy with three-way hash tracking | OpenClaw (no symlink support) |

Copy mode stores a content hash after each sync (`src/sync_state.py`) and compares both sides against it:

| Source | Repo | Result |
|--------|------|--------|
| changed | unchanged | source wins |
| unchanged | changed | repo wins |
| changed | changed | conflict — both kept |
| unchanged | unchanged | no action |

## Conflicts

Nothing is lost in a conflict. Both versions are merged into **one file**, wrapped in `<!-- CONFLICT -->` markers, with `conflict: true` in the frontmatter. This applies to copy-mode and migration conflicts (both sides of the file) and to git rebase conflicts.

## Project Name Mapping (Claude Code)

Claude Code encodes the working directory into the memory folder name (`/home/you/Workspace` → `-home-you-Workspace`), so the same project has a different name on each device. `<data_repo>/claude/projects.conf` maps them to one canonical name:

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
journalctl --user -u mesy -f
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
