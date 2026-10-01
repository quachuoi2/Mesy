# Mesy — Functional Spec

As of 2026-10-01 (branch `main`). Product context: [ProductSpec.md](ProductSpec.md).

## 1. Files

| File | Where | Role |
|------|-------|------|
| `setup.py` | Mesy root | One-time interactive setup per device |
| `sync.py` | Mesy root | Starts the daemon |
| `repos.ini` | Mesy root | Lists data repos. Required |
| `log.log` | Mesy root | Daemon log, 1 MB, one backup |
| `sync.ini` | Data repo root | Timing and targets for that repo |
| `claude/projects.ini` | Data repo | Claude Code project name mapping |
| `.mesy_sync_hashes.json` | Data repo root | Copy-mode hashes for this device. Never committed |
| `.git/mesy_sync_texts.json` | Data repo | Copy-mode text of each file at its last sync on this device, used as the merge base. Never committed |

## 2. Configuration

`repos.ini` — a section without `path`, or whose path is not a folder, is a configuration error (exit 1).

```ini
[repo:alfred-memory]
path = ~/Workspace/AlfredMemory
```

`sync.ini` `[mesy]` — each value must be a finite number above 0.

| Key | Default | Meaning |
|-----|---------|---------|
| `debounce_seconds` | 30 | Quiet time after the last `.md` change before a commit |
| `pull_interval_seconds` | 60 | Time between pulls |
| `scan_interval_seconds` | 60 | Time between scans |

`sync.ini` `[target:<name>]`. The name sets the type: `claude-code`, `codex`, any other name → generic.

| Key | Default | Notes |
|-----|---------|-------|
| `source` | Required. Codex: `$CODEX_HOME`, else `~/.codex` | The tool's folder |
| `repo_subdir` | Required | Folder inside the data repo |
| `file_patterns` | `*.md` | Generic only |
| `exclude` | none | Generic only. Matches names, not paths |
| `sync_mode` | `symlink` | `symlink` or `copy`. Invalid value → exit 1 |

The Claude Code target ignores `source` and `repo_subdir`. It always uses `~/.claude`, `claude/` in the data repo and `claude/projects.ini`.

## 3. Start-up

1. Read `repos.ini`. Missing, empty, or a bad `[repo:*]` section → exit 1.
2. Build each repo's targets. Bad timing value, empty `source` or invalid `sync_mode` → exit 1.
3. No targets in any repo → exit 1.
4. For each repo: pull, scan, then commit and push if the scan succeeded.
5. Enter the watch loop (`watchdog` installed) or the polling loop.

## 4. Sync loop

| Trigger | Action |
|---------|--------|
| `.md` file created, changed or renamed in the data repo (outside `.git`), then `debounce_seconds` of quiet | Scan all targets. If every scan succeeds, commit and push |
| Every `pull_interval_seconds` | `git pull --rebase` |
| Every `scan_interval_seconds` | Scan all targets. No commit; files the scan writes into the repo start the first row |

- Only the data repo is watched. Symlink-mode edits land in the repo directly. Copy-mode edits in the tool folder reach the repo on the next scan.
- Polling mode compares `.md` modification times every `max(debounce_seconds, 60)` seconds, using the smallest value across repos.

Commit and push:

1. Stop if the repo has no changes and the branch is not ahead of `origin`.
2. `git add` the targets' folders only.
3. Commit `sync from <host> at <time>` (ISO 8601, UTC+7), with a list of added, updated and deleted files.
4. `git pull --rebase origin <branch>`. On failure, run conflict handling (section 7).
5. `git push origin <branch>`. On failure, the commit stays local until the next start or the next change.

Every git call times out after 30 s and runs with `GIT_EDITOR=true`, so git never waits for an editor.

## 5. Targets

| Target | Local | Data repo |
|--------|-------|-----------|
| Claude Code | `~/.claude/CLAUDE.md` | `claude/global/CLAUDE.md` |
| Claude Code | `~/.claude/rules/` | `claude/global/rules/` |
| Claude Code | `~/.claude/projects/<raw>/memory/` | `claude/projects/<canonical>/` |
| Codex | `<source>/AGENTS.md` | `<repo_subdir>/AGENTS.md` |
| Codex | `<source>/memories/` | `<repo_subdir>/memories/` |
| Generic | `<source>/**` matching `file_patterns` | `<repo_subdir>/**` |

A generic target whose source is its own repo folder (DMNotes) is not synced in either mode, only committed and pushed.

Claude Code scan order: load mapping → prune `.pending` → sync each mapped project that has `memory/` → record unmapped ones in `.pending`.

## 6. Sync modes

Symlink:

- Real local file → copied into the repo (merged if the repo copy differs), then replaced by a link to it.
- Real local folder → merged into the repo, checked that every file arrived, then replaced by a folder link. Check fails → folder left alone, error logged.
- File only in the repo → link created locally.
- Broken or wrong link → re-pointed at the repo file or folder, if that exists.

Copy (all targets, using per-device hashes):

| Tool side | Repo side | Result |
|-----------|-----------|--------|
| changed | unchanged | Tool side copied to repo |
| unchanged | changed | Repo copied to tool side |
| changed | changed | Merged against the last synced text (section 7) |
| no stored hash | differs | Tool side wins |

- A file on only one side is copied to the other.
- Identical files with no stored hash get one, so the next one-sided edit is copied, not overwritten.
- Folders (Claude Code `memory/` and `rules/`, Codex `memories/`) go through this table file by file, subfolders and every file type included.
- Links left from symlink mode become real files.

Deletes:

- File inside a linked folder (Claude Code `memory/`, Codex `memories/`): deleting it deletes the repo file, so it syncs.
- File-level link (`CLAUDE.md`, `AGENTS.md`, generic files): deleting the link does nothing; the next scan restores it. Delete the file in the data repo instead.
- Copy mode: a file on either side is copied back. It must be removed from both sides on every device.

## 7. Conflicts

Copy-mode and migration conflicts run `git merge-file local base repo`; the result is written to both sides:

- Base: the last synced text in copy mode; empty for migration (a real local file meeting a different repo copy) or when no text is stored yet.
- Edits that don't overlap are combined. Overlapping lines get git's markers; with an empty base, every differing block does:

```markdown
<<<<<<< local (<host>)
...
=======
...
>>>>>>> repo
```

- A file git cannot merge (binary) fails the scan.

Git rebase conflicts keep git's own markers (`HEAD` is the other machine's side); Mesy runs `git add` and continues the rebase. A person resolves marked blocks by hand.

## 8. Unmapped Claude Code projects

- Recorded in `claude/projects/.pending` (committed, shared by all devices): host, time, and one `[YourProjectName] / paths = <raw>` snippet per project.
- Nothing from that project syncs until it is mapped.
- After the raw name is added to `projects.ini`, the next scan syncs the project and removes its snippet. The file is deleted when empty.

## 9. Failure behavior

| Failure | Behavior |
|---------|----------|
| Any target's scan fails | No commits for the whole data repo; pulls continue. Logged once at ERROR until the message changes; INFO on recovery |
| Repo has both `<raw>/` and `<canonical>/` for a mapped project | Scan fails every cycle until the raw folder is merged by hand |
| Pull fails (offline) | Retried next interval |
| Push fails | Warning; commit stays local until the next start or the next change |
| Git call times out | Warning; retried next cycle |
| Misconfiguration at start | Exit 1; systemd restarts every 10 s until fixed |

## 10. Setup and operation

`python3 setup.py` on each device:

1. Prompt for data repos (name, path, git remote) and write `repos.ini`. A path inside the Mesy folder is rejected. Empty folder → clone; folder with files → init, fetch, pull.
2. Offer a template `sync.ini` if missing.
3. Check every target before changing any files.
4. Move existing memory into the repo and create links. Copy-mode generic targets wait for the first daemon run. A Claude Code target in a second repo is skipped.
5. Install the service: systemd user service with lingering (Linux), Task Scheduler task at logon (Windows).

| Task | Linux | Windows |
|------|-------|---------|
| Status | `systemctl --user status mesy` | `schtasks /Query /TN Mesy` |
| Restart | `systemctl --user restart mesy` | `schtasks /End /TN Mesy`, then `/Run` |
| Logs | `tail -f log.log` | `log.log` in the Mesy folder |
