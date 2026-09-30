# Rule breaks (not fixed)

Checked on main at 3ce7281 plus the blank-line pass, 2026-09-30. Rules come from `AlfredMemory/claude/global/rules/code.md` and Mesy's `CLAUDE.md`. Real bugs go in `Bugs.md`, not here.

## Seam Law

| Where | Problem | Fix idea |
|---|---|---|
| `base.py` `_sync_file`, `_sync_dir`; `generic.py` `_sync`, `_walk_src_side`, `_walk_repo_side`; `claude_code.py` `_mapped_pair` | Sync mode (symlink or copy) has two implementations but no interface. Each target checks `if self.sync_mode == "copy"` in several places. | One sync-mode interface with a symlink and a copy implementation, chosen once in the factory. |

## Mesy CLAUDE.md rules

| Rule | Where | Problem |
|---|---|---|
| Misconfiguration exits non-zero | `config.py` `_sync_mode` | Invalid `sync_mode` logs a warning and falls back to symlink, so a typo in `copy` switches the target to symlink mode without stopping |
| Misconfiguration exits non-zero | `config.py` `_parse_repo` | A repo with no path or a missing folder is skipped with a warning; the daemon starts without it |
| Works on Windows | `setup.py:517` `_link_repo_item` | Plain `os.symlink` with no fallback, unlike the other setup link helpers |
