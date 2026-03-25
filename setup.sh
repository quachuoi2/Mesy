#!/usr/bin/env bash
set -euo pipefail

# Mesy — Claude Code memory sync setup
# Run this from inside the cloned Mesy repo on any new server.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$HOME/.claude"

echo "=== Mesy: Claude Memory Sync Setup ==="
echo "Repo: $SCRIPT_DIR"

# 1. Ensure ~/.claude exists
mkdir -p "$CLAUDE_DIR"

# 2. Symlink global memory
if [ -L "$CLAUDE_DIR/memory" ]; then
    echo "[ok] Global memory symlink already exists"
elif [ -d "$CLAUDE_DIR/memory" ]; then
    echo "[migrate] Moving existing global memory into repo..."
    for f in "$CLAUDE_DIR/memory/"*; do
        [ -e "$f" ] || continue
        dest="$SCRIPT_DIR/global/$(basename "$f")"
        if [ ! -e "$dest" ]; then
            cp -r "$f" "$dest"
            echo "  copied $(basename "$f")"
        fi
    done
    rm -rf "$CLAUDE_DIR/memory"
    ln -sfn "$SCRIPT_DIR/global" "$CLAUDE_DIR/memory"
    echo "[ok] Global memory migrated and symlinked"
else
    ln -sfn "$SCRIPT_DIR/global" "$CLAUDE_DIR/memory"
    echo "[ok] Global memory symlinked"
fi

# 3. Symlink project memories
mkdir -p "$CLAUDE_DIR/projects"
for project_dir in "$SCRIPT_DIR/projects/"*/; do
    [ -d "$project_dir" ] || continue
    project_name="$(basename "$project_dir")"
    claude_project_dir="$CLAUDE_DIR/projects/$project_name"

    mkdir -p "$claude_project_dir"
    memory_path="$claude_project_dir/memory"

    if [ -L "$memory_path" ]; then
        echo "[ok] $project_name already symlinked"
    elif [ -d "$memory_path" ]; then
        echo "[migrate] Moving existing memory for $project_name..."
        for f in "$memory_path/"*; do
            [ -e "$f" ] || continue
            dest="$project_dir$(basename "$f")"
            if [ ! -e "$dest" ]; then
                cp -r "$f" "$dest"
                echo "  copied $(basename "$f")"
            fi
        done
        rm -rf "$memory_path"
        ln -sfn "$project_dir" "$memory_path"
        echo "[ok] $project_name migrated and symlinked"
    else
        ln -sfn "$project_dir" "$memory_path"
        echo "[ok] $project_name symlinked"
    fi
done

# 4. Make sync.py executable
chmod +x "$SCRIPT_DIR/sync.py"

# 5. Install systemd user service
mkdir -p "$HOME/.config/systemd/user"
sed "s|%REPO_DIR%|$SCRIPT_DIR|g" "$SCRIPT_DIR/claude-memory-sync.service" \
    > "$HOME/.config/systemd/user/claude-memory-sync.service"

systemctl --user daemon-reload
systemctl --user enable --now claude-memory-sync
echo "[ok] Systemd service installed and started"

# Enable lingering so service runs after logout
loginctl enable-linger "$(whoami)" 2>/dev/null || echo "[warn] Could not enable lingering (may need sudo)"

# 6. Commit and push any migrated files
cd "$SCRIPT_DIR"
git add -A
if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "onboard $(hostname): migrate existing memory"
    git push origin main 2>/dev/null || echo "[warn] Push failed (offline?), will sync later"
fi

echo ""
echo "=== Setup complete ==="
echo "Memory sync daemon is running. Changes will auto-sync to GitHub."
echo "Check status: systemctl --user status claude-memory-sync"
