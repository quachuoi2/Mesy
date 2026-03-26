# Mesy — Claude Code Memory Sync

A seamless Memory Sync mechanism across all your servers. One continuous memory, everywhere.

## How It Works

Claude Code stores memory and instructions as files under `~/.claude/`. Mesy is a git repo that holds all your memory and `CLAUDE.md` files centrally. Symlinks connect Claude Code's expected paths into this repo, and a background daemon auto-commits and pushes/pulls via GitHub.

```
Server A                       GitHub (private repo)                Server B
~/.claude/memory/ (symlink)    Mesy.git                            ~/.claude/memory/ (symlink)
        |                            ^                                    |
        v                            |                                    v
~/Workspace/Mesy/ <-git push/pull-->+<----git push/pull----> ~/Workspace/Mesy/
```

## Repo Structure

```
Mesy/
├── global/                        # User-level memory + global CLAUDE.md
│   ├── CLAUDE.md                  # Synced from ~/.claude/CLAUDE.md
│   └── MEMORY.md
├── projects/                      # Per-project memory (canonical names)
│   ├── Workspace/                 # Canonical project name
│   │   ├── CLAUDE.md              # Synced from ~/.claude/projects/<raw>/CLAUDE.md
│   │   ├── MEMORY.md
│   │   └── *.md
│   └── .pending/                  # Unmapped projects staged here until mapped
│       └── -some-raw-name/
│           ├── .needs_mapping     # Marker with instructions
│           └── *.md
├── sync.py                        # Sync daemon
├── setup.py                       # Cross-platform onboarding (Linux/Windows/WSL)
├── projects.conf                  # Canonical project name mappings
├── claude-memory-sync.service     # Systemd unit template
├── sync.conf                      # Config (intervals)
└── DUAL_BOOT_SETUP.md            # Notes for dual-boot / WSL environments
```

## Setup

### First server (you)

1. Create a private GitHub repo
2. Init git, add remote, push:
   ```bash
   cd ~/Workspace/Mesy
   git init && git add -A && git commit -m "initial commit"
   git remote add origin git@github.com:<you>/Mesy.git
   git push -u origin main
   ```
3. Add your project mappings to `projects.conf`:
   ```ini
   [Workspace]
   paths = -home-allyqch2-Workspace, -C-Users-allyqch2-Workspace
   ```
4. Run setup:
   ```bash
   python3 setup.py
   ```

### Every additional server

```bash
git clone git@github.com:<you>/Mesy.git ~/Workspace/Mesy
cd ~/Workspace/Mesy
# Add this system's raw path names to projects.conf if needed
python3 setup.py
```

That's it. Memory syncs automatically from that point.

## What `setup.py` Does

1. Symlinks `~/.claude/memory/` → `Mesy/global/` and `~/.claude/CLAUDE.md` → `Mesy/global/CLAUDE.md`
2. Reads `projects.conf` and symlinks `~/.claude/projects/<raw-name>/memory/` and `CLAUDE.md` → `Mesy/projects/<canonical>/`
3. Migrates existing raw-named repo directories to canonical names
4. Migrates any existing local memory into the repo, conflict-merging differing files (nothing is lost)
5. Stages unmapped projects in `projects/.pending/` with instructions
6. Installs the sync daemon (systemd on Linux, Task Scheduler on Windows)
7. Enables lingering so the daemon survives logout (Linux)

## What `sync.py` Does

- Watches for `.md` file changes (uses `watchdog` if installed, otherwise polls)
- Debounces 5 seconds after the last change, then commits and pushes
- Pulls remote changes every 60 seconds
- Auto-detects new Claude Code project memory directories:
  - **Mapped** (in `projects.conf`): adopted into the canonical directory and symlinked
  - **Unmapped**: staged in `projects/.pending/` with a `.needs_mapping` marker
- Promotes pending projects when a mapping is added to `projects.conf`
- On merge conflicts: keeps both versions and flags with `conflict: true` in frontmatter
- On file migration conflicts (same filename, different content): merges both versions into one file with conflict markers

## Project Name Mapping

Claude Code encodes the working directory path into the memory folder name (e.g., `/home/user/Workspace` → `-home-user-Workspace`). Different systems produce different names for the same project. `projects.conf` maps them to a canonical name:

```ini
[Workspace]
paths = -home-allyqch2-Workspace, -C-Users-allyqch2-Workspace

[Alfred]
paths = -home-allyqch2-Workspace-Alfred, -C-Users-allyqch2-Workspace-Alfred
```

When the daemon finds an unmapped project, it stages data in `.pending/` and logs a warning. Add the mapping and the next scan cycle promotes it automatically.

## Configuration

Edit `sync.conf`:

```ini
[mesy]
debounce_seconds = 5        # Wait after last change before pushing
pull_interval_seconds = 60  # How often to pull remote changes
scan_interval_seconds = 60  # How often to scan for new projects
```

## Dependencies

- **git** — required
- **Python 3** — required
- **watchdog** (`pip install watchdog`) — optional, falls back to polling

## Managing the Daemon

### Linux (systemd)

```bash
systemctl --user status claude-memory-sync     # Check status
systemctl --user restart claude-memory-sync    # Restart
journalctl --user -u claude-memory-sync -f     # View logs
systemctl --user stop claude-memory-sync       # Stop
```

### Windows (Task Scheduler)

```powershell
schtasks /Query /TN MesyClaudeMemorySync       # Check status
schtasks /Run /TN MesyClaudeMemorySync          # Start
schtasks /End /TN MesyClaudeMemorySync          # Stop
```

## Dual-Boot / WSL

If you use a dual-boot setup (e.g., Ubuntu + Windows/WSL), see [DUAL_BOOT_SETUP.md](DUAL_BOOT_SETUP.md) for path matching and systemd considerations.

## Future: Alfred Integration

Mesy is designed to evolve into Alfred's memory subsystem:

- **Phase 2**: Alfred gains a `MemoryManager` that reads/writes/searches this repo directly
- **Phase 4**: The sync daemon becomes an Alfred module; clients push memory via Alfred's API instead of git
