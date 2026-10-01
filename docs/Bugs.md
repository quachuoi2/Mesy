# Known bugs (not fixed)

Checked against the code on main, 2026-09-30.

| # | Bug | Where | Hits today? |
|---|---|---|---|
| 2 | Conflict merge breaks nested frontmatter and drops repo values | `src/conflict.py` `split_frontmatter`, `merge_conflicting_file` | Yes, on any memory-file conflict |
| 3 | Rebase conflict labels are swapped | `src/git_sync.py:126-128` | Yes, on any rebase conflict |
| 4 | A repeat rebase conflict adds a second `conflict: true` line | `src/conflict.py` `flag_conflict` | Only when an already-flagged file conflicts again |
| 7 | Claude Code copy mode syncs two local files to one repo file | `src/targets/claude_code.py` | No (neither file pair exists today) |
| 8 | Failed pulls are logged at debug only | `src/git_sync.py` `pull_remote` | Yes, silently |

## 2. Conflict merge breaks frontmatter

`split_frontmatter` reads only `key: value` lines, one per line.

- **Nested keys are flattened.** `metadata:` has no `": "`, so it is dropped, and the indented `  type: feedback` becomes a top-level `type`. Every memory file written by current Claude Code uses nested `metadata:`.
- **Multi-line values are cut** to the lines that contain `": "`.
- **Repo values are lost:** `{**repo_fm, **local_fm}` keeps only the local value for keys both sides have, so a repo-side `description` change is lost. This breaks the "nothing is lost in a conflict" rule.
- **Fix idea:** keep both frontmatter blocks as raw text inside the conflict sections, and add only `conflict: true` at the top.

## 3. Rebase conflict labels are swapped

- **What happens:** during `git pull --rebase`, the `<<<<<<<` side is the remote commit (the rebase base) and the `>>>>>>>` side is the local commit being replayed. `_replace_markers` labels the first side "local (hostname)" and the second "remote".
- **Effect:** a person resolving the conflict keeps the wrong version.
- **Fix:** swap the two labels. Add a test with a real rebase-conflict text.

## 4. A repeat rebase conflict adds a second flag

- **What happens:** `flag_conflict` always inserts `conflict: true`. A file that already has it (an earlier conflict not yet resolved) gets a second `conflict:` line.
- **Effect:** duplicate key in the frontmatter. Most YAML readers keep the last one, so the flag still reads as true.
- **Fix:** skip the line when the key already exists.

## 7. Claude Code copy mode: two local files, one repo file

- **What happens:** `~/.claude/projects/<x>/CLAUDE.md` and `~/.claude/projects/<x>/memory/CLAUDE.md` both map to `<canonical>/CLAUDE.md` in the repo. If both exist, they overwrite each other on every scan.
- **Fix:** remove the project `CLAUDE.md` sync. Claude Code never reads that file.

## 8. Failed pulls are hidden

- **What happens:** `pull_remote` logs any non-zero `git pull --rebase` at debug level with the message "offline?".
- **Effect:** a pull refused because of uncommitted changes, or any other git error, looks the same as being offline and never appears in `log.log` at the default level. This is how the rename-save bug (Point 3) stayed hidden.
- **Fix:** log at warning, once per distinct error, the same way scan failures are logged.
