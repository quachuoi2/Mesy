# Claude Code memory system

Summary of the official Claude Code documentation on memory: CLAUDE.md files, rules,
auto memory (`MEMORY.md`), and the settings that control them. Fetched 2026-09-26.

Sources:

- [How Claude remembers your project](https://code.claude.com/docs/en/memory)
- [Subagents: persistent memory](https://code.claude.com/docs/en/sub-agents#enable-persistent-memory)
- [Settings files and precedence](https://code.claude.com/docs/en/settings)

## Two memory systems

Both load at the start of every session. Both are context, not enforced configuration.
To block an action no matter what Claude decides, use a `PreToolUse` hook.

|                  | CLAUDE.md files                        | Auto memory                                          |
| :--------------- | :------------------------------------- | :--------------------------------------------------- |
| Who writes it    | You                                    | Claude                                               |
| Contains         | Instructions and rules                 | Learnings and patterns                               |
| Scope            | Project, user, or organization         | Per repository, shared across worktrees              |
| Loaded           | Every session, in full (up to 4 MiB)   | Every session, first 200 lines or 25KB of `MEMORY.md` |
| Use for          | Standards, workflows, architecture     | Preferences, corrections, context not in the code    |

## CLAUDE.md files

### Locations (load order: broadest first)

| Scope                | Location                                                                 | Shared with                  |
| -------------------- | ------------------------------------------------------------------------ | ---------------------------- |
| Managed policy       | macOS: `/Library/Application Support/ClaudeCode/CLAUDE.md`              | All users on the computer    |
|                      | Linux and WSL: `/etc/claude-code/CLAUDE.md`                              |                              |
|                      | Windows: `C:\Program Files\ClaudeCode\CLAUDE.md`                         |                              |
| User                 | `~/.claude/CLAUDE.md`                                                    | Just you, all projects       |
| Project              | `./CLAUDE.md` or `./.claude/CLAUDE.md`                                   | Team, via source control     |
| Local                | `./CLAUDE.local.md` (add to `.gitignore`)                                | Just you, this project       |

Managed content can also go inline in `managed-settings.json` under the `claudeMd` key.
That key is ignored in user, project, and local settings.

### How files load

- Claude Code walks from the working directory **up** to the filesystem root and loads
  every `CLAUDE.md` and `CLAUDE.local.md` it finds.
- Files are **concatenated**, not overridden. Order: root first, working directory last.
  Within one directory, `CLAUDE.local.md` comes after `CLAUDE.md`.
- `CLAUDE.md` files in **subdirectories** below the working directory load on demand,
  when Claude reads a file in that subdirectory.
- Block-level HTML comments (`<!-- note -->`) are stripped before loading. Comments
  inside code blocks are kept.
- Content is delivered as a user message after the system prompt, not inside it.
  For true system-prompt text, use `--append-system-prompt`.
- Files over 4 MiB are skipped. Target under 200 lines per file.

Example: running in `foo/bar/` loads `foo/CLAUDE.md`, then `foo/bar/CLAUDE.md`.

### Additional directories

`--add-dir` gives access to other directories but does not load their CLAUDE.md by default.

```bash
CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1 claude --add-dir ../shared-config
```

This loads `CLAUDE.md`, `.claude/CLAUDE.md`, `.claude/rules/*.md`, and `CLAUDE.local.md`
from the added directory.

### Imports

```text
See @README for project overview and @package.json for npm commands.
- git workflow @docs/git-instructions.md
- @~/.claude/my-project-instructions.md
```

| Rule                    | Detail                                                                 |
| ----------------------- | ---------------------------------------------------------------------- |
| Path resolution         | Relative to the file containing the import, not the working directory  |
| Absolute and `~` paths  | Allowed                                                                |
| Recursion               | Maximum depth of four hops                                             |
| Code spans and fences   | Not parsed; write `` `@README` `` to keep the text literal             |
| Context cost            | Imported files load at launch, so imports do not save context          |
| External imports        | A project file importing outside the working directory shows a one-time approval dialog. Declining disables them for good |
| User-scope files        | `~/.claude/CLAUDE.md` and `~/.claude/rules/` imports load without the dialog |

Worktrees: a gitignored `CLAUDE.local.md` exists only in the worktree where it was
created. To share personal instructions across worktrees, import a file from `~`.

**Cowork sessions (desktop):** skip user-scope imports that resolve outside the working
directory, skip a `~/.claude/CLAUDE.md` that is itself a symlink or hard link, and skip
symlinked `~/.claude/rules/` entries that point outside the working directory.

### Writing guidance

- Add an entry when Claude repeats a mistake, a review catches something Claude should
  have known, or you type the same correction twice.
- Keep to facts needed in every session. Multi-step procedures belong in skills;
  area-specific rules belong in path-scoped rules.
- Be concrete: "Use 2-space indentation", not "Format code properly".
- Remove contradictions; with two conflicting rules, Claude may pick either.
- `/init` generates a starting CLAUDE.md, or suggests improvements to an existing one.
  `CLAUDE_CODE_NEW_INIT=1` makes it an interactive flow that can also set up skills and hooks.
- `/doctor` proposes trims for a checked-in CLAUDE.md.

## Rules (`.claude/rules/`)

```text
your-project/.claude/
├── CLAUDE.md
└── rules/
    ├── code-style.md
    ├── testing.md
    └── frontend/react.md   # discovered recursively
```

- Rules **without** `paths` load at launch, same priority as `.claude/CLAUDE.md`.
- Rules **with** `paths` load only when Claude reads a matching file.
- `paths` is the only frontmatter field read. Invalid YAML means the rule loads as if it
  had no `paths` (see `claude --debug`).

```markdown
---
paths:
  - "src/**/*.{ts,tsx}"
  - "tests/**/*.test.ts"
---

# API Development Rules
- All API endpoints must include input validation
```

| Pattern                | Matches                                 |
| ---------------------- | --------------------------------------- |
| `**/*.ts`              | All TypeScript files anywhere           |
| `src/**/*`             | Everything under `src/`                 |
| `*.md`                 | Markdown files in the project root      |

- Brace expansion budget: 1,000 expanded patterns and 4 MiB per rule.
- Escape a literal `[` as `\[`.
- **User-level rules:** `~/.claude/rules/` applies to every project and loads before
  project rules. Neither overrides the other.
- **Symlinks** are supported, including directories. A symlink pointing outside the
  working directory is treated like an external import; to share rules without that,
  put them in `~/.claude/rules/`.

### Excluding files

`claudeMdExcludes` skips CLAUDE.md or rules files by absolute-path glob. Works at every
settings layer; arrays merge. Managed policy CLAUDE.md cannot be excluded.

```json
{
  "claudeMdExcludes": [
    "**/monorepo/CLAUDE.md",
    "/home/user/monorepo/other-team/.claude/rules/**"
  ]
}
```

## AGENTS.md

| Repository has                                               | Claude reads                          |
| :----------------------------------------------------------- | :------------------------------------ |
| `AGENTS.md`, no `CLAUDE.md`/`CLAUDE.local.md` on the path    | `AGENTS.md`                           |
| `AGENTS.md` and a `CLAUDE.md` or `CLAUDE.local.md`           | `CLAUDE.md` files only                |
| `CLAUDE.md` containing `@AGENTS.md`                          | `CLAUDE.md` with `AGENTS.md` imported |

- `~/.claude/CLAUDE.md`, managed CLAUDE.md, and `.claude/rules/` do **not** count for
  this check; they load alongside `AGENTS.md`.
- Not read: `AGENTS.local.md`, `AGENTS.override.md`, anything under `.agents/`.
- Requires v2.1.277 or later.

Change the behavior with `/config` > **Project instructions**, or in
`~/.claude/settings.json` (ignored in project and local settings):

| Value                     | Reads                                                           |
| :------------------------ | :-------------------------------------------------------------- |
| `claude-md-or-agents-md`  | Default, as in the table above                                  |
| `claude-md-and-agents-md` | Both; each directory's `CLAUDE.md` first, then `AGENTS.md`      |
| `claude-md`               | `CLAUDE.md` only                                                |
| `managed-only`            | Managed CLAUDE.md and auto memory only                          |

```json
{
  "pluginConfigs": {
    "agents-md@builtin": {
      "options": { "instructionFiles": "claude-md-and-agents-md" }
    }
  }
}
```

## Auto memory

### What Claude saves

Each memory is one file with a `type` in its frontmatter:

| Type        | Holds                                                           |
| ----------- | --------------------------------------------------------------- |
| `user`      | Your role, expertise, working preferences                       |
| `feedback`  | Corrections you give and approaches you confirm                 |
| `project`   | Ongoing work, deadlines, decisions not derivable from code/git  |
| `reference` | Where to find outside information (issue tracker, dashboard)    |

Claude skips anything derivable from the code (architecture, file paths, fixes) and
anything already in CLAUDE.md. Asking "remember X" saves to auto memory; asking "add
this to CLAUDE.md" edits CLAUDE.md.

When Claude writes a memory file that has frontmatter, Claude Code adds or updates a
`modified` field (ISO 8601). Files without frontmatter never get one. Requires v2.1.214.

### Storage

```text
~/.claude/projects/<project>/memory/
├── MEMORY.md            # index, one line per memory, loaded every session
├── user_role.md         # one memory
└── feedback_testing.md  # one memory
```

| Fact                     | Detail                                                                  |
| ------------------------ | ----------------------------------------------------------------------- |
| `<project>` name         | Derived from the git repository; all worktrees and subdirectories share it. Outside git, the project root is used |
| Machine-local            | Not shared across computers or cloud environments                       |
| Retention                | Not removed by the `cleanupPeriodDays` transcript cleanup               |
| `MEMORY.md` load limit   | First 200 lines or 25KB, whichever comes first; the rest is dropped     |
| Over the limit           | Write succeeds, but Claude Code returns an error telling Claude to rewrite the index |
| Topic files              | Not loaded at startup; Claude reads them on demand                      |
| Subagents                | Do not get the main auto memory (forks do)                              |

### Controls

| Setting / variable                     | Effect                                                             |
| -------------------------------------- | ------------------------------------------------------------------ |
| `/memory` toggle                       | Saves `autoMemoryEnabled` to `~/.claude/settings.json`             |
| `"autoMemoryEnabled": false`           | Turns auto memory off (can be set per project)                     |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`    | Turns auto memory off                                              |
| `"autoMemoryDirectory": "~/dir"`       | Moves storage; absolute or `~/` path; any settings scope. From project settings it follows workspace trust rules |
| `CLAUDE_CODE_PROJECT_DIR_NAME`         | With `CLAUDE_CONFIG_DIR`, fixes the `<project>` name for every repository (v2.1.234+) |

### Subagent memory

Set `memory: <scope>` in a subagent's frontmatter. It gets the first 200 lines or 25KB
of its own `MEMORY.md` plus Read, Write, and Edit tools. Disabled when auto memory is off.

| Scope     | Location                                      |
| :-------- | :-------------------------------------------- |
| `user`    | `~/.claude/agent-memory/<agent>/`             |
| `project` | `.claude/agent-memory/<agent>/`               |
| `local`   | `.claude/agent-memory-local/<agent>/`         |

## Settings files (preferences)

Higher rows override lower rows. Arrays (for example `claudeMdExcludes`, permission
rules) merge across layers instead of overriding.

| Precedence | Scope          | File                          | Affects                     |
| ---------- | -------------- | ----------------------------- | --------------------------- |
| 1          | Managed        | `managed-settings.json`, MDM  | Everyone the org deploys to |
| 2          | Command line   | `claude --settings`           | This session                |
| 3          | Project local  | `.claude/settings.local.json` | You, this project           |
| 4          | Shared project | `.claude/settings.json`       | Everyone in the project     |
| 5          | User           | `~/.claude/settings.json`     | You, every project          |

## Commands and debugging

| Command / tool             | Use                                                                   |
| -------------------------- | --------------------------------------------------------------------- |
| `/memory`                  | List and open memory files, toggle auto memory, open the memory folder |
| `/context`                 | See which CLAUDE.md and rules files loaded (**Memory files**)          |
| `/init`                    | Generate or improve CLAUDE.md; imports Cursor and Copilot rules        |
| `/import`                  | Copy another agent's config (for example `AGENTS.md`) into Claude Code |
| `InstructionsLoaded` hook  | Log which instruction files load, when, and why                       |
| `claude --debug`           | Show rule frontmatter parse errors                                    |

After `/compact`: the project-root CLAUDE.md is re-read from disk. Nested CLAUDE.md files
and path-scoped rules reload only when Claude next reads a matching file. Instructions
given only in conversation are lost.

## Notes for Mesy

- Auto memory is machine-local by design; Mesy is what shares it across computers.
- The `<project>` directory name comes from the local repository path, so it differs
  between computers. This is why `projects.conf` maps per-device names to canonical ones.
- `~/.claude/memory/` is not a documented location. Claude Code does not load it.
- Symlinked `~/.claude/CLAUDE.md` works in the CLI but is skipped in desktop Cowork sessions.
- `MEMORY.md` beyond 200 lines or 25KB is silently dropped at load, including after a
  merge that grows it.
