#!/bin/bash
# Stop the Tilia launchd agent and remove its plist, and the "Tilia Mirror.html" alias in
# your home folder if it points into the data folder. Your data folder is left alone.
set -euo pipefail

LABEL="com.tilia.agent"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$DEST"
echo "Stopped and removed $LABEL"

# The alias is a symlink setup made. Only a symlink into the data folder is ours; a real
# file, or a link that points anywhere else, stays.
ALIAS="$HOME/Tilia Mirror.html"
DATA="${TILIA_HOME:-$HOME/Library/Application Support/Tilia}"
while [ "${DATA%/}" != "$DATA" ]; do DATA="${DATA%/}"; done
if [ -L "$ALIAS" ]; then
  TARGET="$(readlink "$ALIAS")"
  REAL_DATA="$(cd "$DATA" 2>/dev/null && pwd -P || true)"
  case "$TARGET" in
    "$DATA"/*|"${REAL_DATA:-/nonexistent}"/*)
      rm -f "$ALIAS"
      echo "Removed ~/Tilia Mirror.html (the alias; the page itself is in your data folder)"
      ;;
  esac
fi
