# Mesy — Sync Flow Scenarios

As of 2026-10-01 (branch `main`, after `1a74924`). Behavior reference: [FunctionalSpec.md](FunctionalSpec.md). Known bugs: [Bugs.md](Bugs.md), cited as "Bugs #n".

"Tested" means reproduced with Mesy's own `src/git_sync.py` functions on throwaway repos, with no `TERM` and no `EDITOR` set, the same as the systemd service.

Timings below use AlfredMemory's `sync.ini`: quiet time (`debounce_seconds`) 600 s, pull interval 600 s, scan interval 600 s.

## Summary

| # | Scenario | Status |
|---|----------|--------|
| 1 | Normal: this machine is up to date and edits | Works |
| 2 | Another machine pushed, this machine has no edits | Works |
| 3 | Two machines changed different files | Works |
| 4 | Two machines changed the same file, different lines | Works |
| 5 | Two machines changed the same lines, or created the same new file | Works |
| 6 | One machine deleted a file, the other edited it | **Broken** (new finding) |
| 7 | Deletes without a conflict | Partly |
| 8 | Offline, or push rejected | Partly |
| 9 | Switching machines before the first one pushed | Partly |
| 10 | Machine sleeps, shuts down, or the daemon stops | Partly |
| 11 | A target's scan fails | Partly |
| 12 | Config files edited by hand | Missing |
| 13 | Config changes arrive from another machine | Partly |
| 14 | New machine joins | Works |
| 15 | Copy-mode targets (OpenClaw, DMNotes) | Partly |
| 16 | Rare git states | Missing |

## 1. Normal: this machine is up to date and edits

The order in code is commit, then pull, then push (`src/git_sync.py:34-51`):

1. A `.md` file changes in the data repo. Watchdog records the time.
2. After 600 s with no further change: scan all targets.
3. Every scan succeeded: `git add` the target folders, commit.
4. `git pull --rebase` (nothing new), `git push`.

Handled:
- While the edit waits for the quiet time, interval pulls fail, because git refuses to pull with uncommitted edits to tracked files (tested). Harmless: the commit's own pull in step 4 catches up.

## 2. Another machine pushed, this machine has no edits

1. Interval pull (every 600 s) applies the remote commits.
2. Symlink mode: edited files are visible at once, since local links point into the repo.
3. New files get their link or copy at the next scan (watchdog after the quiet time, or the scan interval).

Handled: all of the above. Not handled: see 10 (retry after waking from sleep) and 7 (deleted files).

## 3. Two machines changed different files

`git pull --rebase` replays this machine's commit on top of the remote one without conflict, then pushes. Handled.

## 4. Two machines changed the same file, different lines

Git merges the lines during the rebase. Handled.

Watch out: two machines each adding a line at the **end** of `MEMORY.md` is the same spot to git. That is scenario 5, not this one.

## 5. Two machines changed the same lines, or created the same new file

Flow now:

1. `git pull --rebase` stops on the conflict.
2. `resolve_conflicts` leaves git's markers as they are and runs `git add`.
3. `git rebase --continue` finishes in one pass. Mesy runs git with `GIT_EDITOR=true`, so git never waits for an editor.
4. The markers are committed and pushed. A person resolves them by hand.

## 6. One machine deleted a file, the other edited it

1. `git pull --rebase` stops. Git leaves the edited version in the folder, with no markers.
2. `_resolve_file` (`src/git_sync.py:111-121`) skips files without `<<<<<<<` and never runs `git add` on them.
3. `rebase --continue` fails (file still unresolved). `resolve_conflicts` only aborts when nothing is unresolved, so it does nothing.
4. Every later pull repeats steps 2-3 (tested: still stopped after a second pull). Push is rejected each time.

Not handled: this whole case. Binary files hit the same path (no markers). Not tested: whether a later Mesy commit (`git add` of the target folders) clears it.

## 7. Deletes without a conflict

Rules per link type are in [FunctionalSpec.md §6](FunctionalSpec.md) (Deletes). Flow gaps:

- Deleted in the repo by another machine: file-level links (`~/.claude/CLAUDE.md`, `AGENTS.md`) on this machine are left pointing at nothing. The scan skips anything that is already a link.
- Copy mode: a file deleted on one side is copied back from the other. Deletes never spread.

## 8. Offline, or push rejected

1. Commit made locally.
2. Pull fails. Warning `pull failed`.
3. Push fails. Warning `push failed (offline?)`.
4. Back online: the next interval pull replays local commits on top of the remote. Conflicts go to scenario 5 or 6.

Not handled:
- **No push retry while running.** Commits left by a failed or rejected push go out at the next start or the next change, but not on the pull timer.
- A login or SSH key failure looks the same as offline in the log.

## 9. Switching machines before the first one pushed

Worst case for an edit on machine A to reach machine B:

| Step | Delay |
|------|-------|
| A: quiet time after the last edit | 600 s |
| B: next interval pull | up to 600 s |
| Copy-mode tool folders only: scan on A, scan on B | up to 600 s each |

So B can be 20 minutes behind (40 for copy-mode targets). If B edits the same file in that window, the later push hits scenario 5.

Not handled: nothing makes A push sooner when the person leaves it (see 10).

## 10. Machine sleeps, shuts down, or the daemon stops

Handled:
- Wake from sleep: timers use the wall clock, so the first loop tick after waking sees the pull interval has passed and pulls at once.
- Next start after a stop: `first_sync` pulls, scans, commits, and the commit's own pull and push go through. Symlink-mode edits made while the daemon was off are already in the repo folder, so they are included.

Not handled:
- After waking, the network is often not up yet. That pull fails, and the next try is a full 600 s later, because `run_intervals` (`src/cycle.py:67-70`) records the pull time before the pull runs, even when it fails.
- Shutdown, `systemctl stop` or restart: SIGTERM ends Python at once (only Ctrl+C is caught). No final commit or push, so edits within the last 600 s stay on this machine until it starts again.

## 11. A target's scan fails

No commits for the whole data repo. The README says "Pulls continue, so the repo syncs one-way". That is true only while no tracked file has uncommitted edits. The first local edit to an existing memory file makes git refuse every pull (tested), so the machine stops syncing in both directions. New files do not block pulls.

## 12. Config files edited by hand

- `git add` stages only the target folders (`claude/global/`, `claude/projects/`, `openclaw/main/`). `sync.ini` and `claude/projects.ini` are tracked but outside them, so Mesy never commits them.
- Edited and not committed by hand: every `git pull --rebase` is refused (tested). This machine stops getting remote changes, and its pushes are rejected once GitHub moves ahead. The pull failure is a warning; the push failure says "offline?".
- Today the person commits them by hand (e.g. `545d215` "Added new project paths"), which avoids this.

## 13. Config changes arrive from another machine

Handled:
- `projects.ini` is re-read on every scan. A new mapping takes effect at the next scan: the raw repo folder is renamed and its `.pending` entry removed.

Not handled:
- `sync.ini` is read only at start. Every machine needs a daemon restart to pick up a change.
- A bad value pushed in `sync.ini`: each machine exits with code 1 at its next restart, and systemd retries every 10 s. The daemon never runs, so it never pulls the fix. It needs a manual `git pull` on each machine.

## 14. New machine joins

Handled (`setup.py`):
- Empty folder: clone. Folder with files: `git init`, fetch, `git pull --allow-unrelated-histories`, set upstream.
- Local memory is moved into the repo and linked. Files that differ from the repo copy are merged with no base, so every differing block is marked. The daemon's `first_sync` commits and pushes the result.
- Unmapped projects are listed in `claude/projects/.pending`.

Mismatch: `.pending` is in AlfredMemory's `.gitignore`, so each machine keeps its own. [FunctionalSpec.md §8](FunctionalSpec.md) says it is committed and shared. Keep it ignored: `add_pending` rewrites its `discovered:` time on every scan, so if it were tracked it would block pulls the same way as scenario 12.

## 15. Copy-mode targets (OpenClaw, DMNotes)

Flow differences from symlink mode:
- The tool folder is not watched. An edit there reaches the repo at the next scan, then waits the quiet time before the commit.
- Received changes reach the tool folder at the next scan on the receiving machine.
- Both sides changed on this machine between two scans: merged against the last synced text before git is involved; only overlapping lines are marked. Handled.

Not handled:
- Deletes never spread (scenario 7).

## 16. Rare git states

- `git add` with several folders fails completely if one of them is missing (tested: nothing staged). Happens when a generic target's source folder is missing on this machine and no machine has created its repo folder yet. Blocks commits for every target in that repo.
- During a stopped rebase the repo is not on a branch, so `current_branch` falls back to `"main"`. Wrong for a data repo whose branch has another name.
- A git command killed by the 30 s timeout, or by the daemon being killed, can leave `.git/index.lock`. Every later git call then fails until the file is removed by hand. Not tested.
