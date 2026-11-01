#!/usr/bin/env bash
# Install / remove the alert-fade forward record job (login/wake + daily 21:00 local).
#   scripts/install_alert_fade_forward.sh            # install
#   scripts/install_alert_fade_forward.sh --remove
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=local.sgt.alert-fade-forward
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$DEST"; echo "removed $LABEL"; exit 0
fi
[[ -x "$REPO/.venv/bin/python" ]] || { echo "missing $REPO/.venv"; exit 1; }
mkdir -p "$REPO/reports/alert_fade" "$HOME/Library/LaunchAgents"
sed "s|__REPO__|$REPO|g" "$REPO/deploy/launchd/sgt.alert-fade-forward.plist.template" > "$DEST"
plutil -lint "$DEST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "installed $LABEL (login/wake + daily 21:00); log: reports/alert_fade/run.log"
