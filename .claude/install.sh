#!/bin/bash

SCRIPT_PATH="$(realpath "${BASH_SOURCE}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

cp -R $SCRIPT_DIR/hooks ~/.claude

# Merge settings.json: existing settings preserved, dotfile wins for conflicts
DOTFILE_SETTINGS="$SCRIPT_DIR/settings.json"
DEST_SETTINGS="$HOME/.claude/settings.json"

if [ -f "$DOTFILE_SETTINGS" ]; then
    if [ -f "$DEST_SETTINGS" ]; then
        jq -s '.[0] * .[1]' "$DEST_SETTINGS" "$DOTFILE_SETTINGS" > /tmp/claude_merged_settings.json \
            && mv /tmp/claude_merged_settings.json "$DEST_SETTINGS"
    else
        cp "$DOTFILE_SETTINGS" "$DEST_SETTINGS"
    fi
fi