# Mesy — Product Spec

As of 2026-09-26 (branch `refactor`). Behavior details: [FunctionalSpec.md](FunctionalSpec.md).

## Summary

Mesy is a background program that keeps AI assistant memory files identical on every device a person uses. It stores them in a private GitHub repository and commits, pushes and pulls changes without user action.

## Problem

- AI tools keep memory in local files, so each device builds its own separate memory.
- Claude Code names project folders after the full path (`/home/you/Workspace` → `-home-you-Workspace`), so one project has a different name on each device.
- Some tools (OpenClaw) do not follow symbolic links.
- Copying by hand is slow and loses edits when two devices change the same file.

## Users

One person with several devices (Linux, Windows, WSL, dual-boot) who owns a private GitHub repository and can edit INI config files.

## Goals

- One shared memory per project across all devices.
- Runs in the background and starts at login.
- Never loses content: edits that don't overlap are merged, overlapping ones are both kept and marked.
- Works offline; catches up when the network returns.
- Several data repositories per device (for example personal memory and team notes).

## Non-goals

- Real-time sync. Changes wait for a quiet period (default 30 s).
- Resolving overlapping edits. They are marked for a person to resolve.
- Syncing anything other than memory files. Only `.md` changes start a sync.
- A server or API. GitHub is the only shared store.

## Supported tools

| Tool | What syncs | Mode |
|------|-----------|------|
| Claude Code | `~/.claude/CLAUDE.md`, each project's `CLAUDE.md` and `memory/` folder | symlink (default) or copy |
| Codex | `~/.codex/AGENTS.md`, `~/.codex/memories/` | symlink (default) or copy |
| Any Markdown folder (e.g. OpenClaw) | Files matching patterns, minus exclusions | symlink (default) or copy |

## Key features

- Maps each device's Claude Code project name to one shared name (`projects.ini`).
- Lists unmapped projects in a shared `.pending` file with a ready-to-paste snippet.
- Two sync modes: symbolic links into the repo, or two-way copies for tools that cannot follow links.
- Marks conflicts with git's own markers, so editors like VS Code offer one-click resolution.
- Interactive setup: registers repos, clones them, moves existing memory in, installs the background service.

## Constraints

| Item | Requirement |
|------|-------------|
| Platforms | Linux (systemd user service), Windows (Task Scheduler), WSL |
| Runtime | Python 3 and `git` |
| Optional | `watchdog` for instant change detection; without it, checks every 60 s or more |
| Storage | One private GitHub repository per data repo, with git access set up on each device |
