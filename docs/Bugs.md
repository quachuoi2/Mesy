# Known bugs (not fixed)

Checked against the code on main, 2026-09-30.

| # | Bug | Where | Hits today? |
|---|---|---|---|
| 8 | Failed pulls are logged at debug only | `src/git_sync.py` `pull_remote` | Yes, silently |

## 8. Failed pulls are hidden

- **What happens:** `pull_remote` logs any non-zero `git pull --rebase` at debug level with the message "offline?".
- **Effect:** a pull refused because of uncommitted changes, or any other git error, looks the same as being offline and never appears in `log.log` at the default level. This is how the rename-save bug (Point 3) stayed hidden.
- **Fix:** log at warning, once per distinct error, the same way scan failures are logged.
