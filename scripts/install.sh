#!/bin/bash
# Install and start the Tilia launchd agent (com.tilia.agent).
# Fills launchd/com.tilia.agent.plist.template with this repo, its .venv Python and the
# data folder, writes ~/Library/LaunchAgents/com.tilia.agent.plist and bootstraps it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.tilia.agent"
SRC="$REPO/launchd/$LABEL.plist.template"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
PY="$REPO/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "No Python at $PY. Run ./setup first." >&2
  exit 1
fi

# The data folder, as Tilia itself resolves it (honours TILIA_HOME; refuses the repo).
DATA="$(cd "$REPO" && "$PY" -c 'from tilia import paths; print(paths.data_dir())')"

# launchd starts with a bare PATH; make sure `claude`, pmset and caffeinate are reachable.
AGENT_PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if CLAUDE_BIN="$(command -v claude 2>/dev/null)"; then
  AGENT_PATH="$(dirname "$CLAUDE_BIN"):$AGENT_PATH"
fi

# The agent's private TMPDIR (see the plist), never the shared /tmp.
mkdir -p "$DATA/tmp" && chmod 700 "$DATA/tmp"
# launchd opens agent.log itself; the plist's Umask (077) keeps new ones private, and an
# existing one from before that setting is tightened here.
if [ -f "$DATA/agent.log" ]; then chmod 600 "$DATA/agent.log"; fi
mkdir -p "$HOME/Library/LaunchAgents"
"$PY" - "$SRC" "$DEST" "$PY" "$REPO" "$DATA" "$AGENT_PATH" <<'PYEOF'
import sys
from xml.sax.saxutils import escape
src, dest, py, repo, data, path = sys.argv[1:]
text = open(src, encoding="utf-8").read()
for key, val in (("__PYTHON__", py), ("__REPO__", repo), ("__DATA__", data), ("__PATH__", path)):
    text = text.replace(key, escape(val))
open(dest, "w", encoding="utf-8").write(text)
PYEOF
plutil -lint "$DEST" >/dev/null

# Remove any previously loaded copy; fine if none exists.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
# wait for the old copy to fully unload (up to 15 s), else bootstrap fails with "Bad request"
for _ in $(seq 1 30); do
  launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done
for attempt in 1 2 3; do
  launchctl bootstrap "$DOMAIN" "$DEST" && break
  [ "$attempt" = 3 ] && exit 1
  sleep 2
done

echo "Installed and started $LABEL"
echo "Log:  $DATA/agent.log   (tail -f to watch)"
echo "Stop: scripts/uninstall.sh"
