# Mesy — Claude Code Memory Sync

A seamless Memory Sync mechanism across all your servers. One continuous memory, everywhere.

## How It Works

Claude Code stores memory as markdown files under `~/.claude/`. Mesy is a git repo that holds all your memory centrally. Symlinks connect Claude Code's expected paths into this repo, and a background daemon auto-commits and pushes/pulls via GitHub.

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
├── global/                        # User-level memory (shared across all projects)
│   └── MEMORY.md
├── projects/                      # Per-project memory (Claude Code's encoded paths)
│   └── -home-user-Workspace/
│       ├── MEMORY.md
│       └── *.md
├── sync.py                        # Sync daemon
├── setup.sh                       # New server onboarding
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
3. Run setup:
   ```bash
   ./setup.sh
   ```

### Every additional server

```bash
git clone git@github.com:<you>/Mesy.git ~/Workspace/Mesy
cd ~/Workspace/Mesy
./setup.sh
```

That's it. Memory syncs automatically from that point.

## What `setup.sh` Does

1. Symlinks `~/.claude/memory/` → `Mesy/global/`
2. Symlinks `~/.claude/projects/*/memory/` → matching dirs in `Mesy/projects/`
3. Migrates any existing local memory into the repo (nothing is lost)
4. Installs and starts the systemd sync daemon
5. Enables lingering so the daemon survives logout

## What `sync.py` Does

- Watches for `.md` file changes (uses `watchdog` if installed, otherwise polls)
- Debounces 5 seconds after the last change, then commits and pushes
- Pulls remote changes every 60 seconds
- Auto-detects new Claude Code project memory directories and adopts them with symlinks
- On merge conflicts: keeps both versions and flags with `conflict: true` in frontmatter

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

```bash
# Check status
systemctl --user status claude-memory-sync

# Restart
systemctl --user restart claude-memory-sync

# View logs
journalctl --user -u claude-memory-sync -f

# Stop
systemctl --user stop claude-memory-sync
```

## Dual-Boot / WSL

If you use a dual-boot setup (e.g., Ubuntu + Windows/WSL), see [DUAL_BOOT_SETUP.md](DUAL_BOOT_SETUP.md) for path matching and systemd considerations.

## Future: Alfred Integration

Mesy is designed to evolve into Alfred's memory subsystem:

- **Phase 2**: Alfred gains a `MemoryManager` that reads/writes/searches this repo directly
- **Phase 4**: The sync daemon becomes an Alfred module; clients push memory via Alfred's API instead of git
