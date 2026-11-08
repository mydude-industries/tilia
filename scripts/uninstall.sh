#!/bin/bash
# Stop the Tilia launchd agent and remove its plist. Your data folder is left alone.
set -euo pipefail

LABEL="com.tilia.agent"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$DEST"
echo "Stopped and removed $LABEL"
