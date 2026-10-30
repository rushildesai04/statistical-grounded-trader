#!/usr/bin/env bash
# Install / remove the always-on signals bot as a launchd user agent.
#   scripts/install_signals_live.sh --observe     # listen + notify, never order
#   scripts/install_signals_live.sh --paper       # trade the signals paper account
#   scripts/install_signals_live.sh --remove
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=local.sgt.signals-live
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
case "${1:-}" in
  --remove)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$DEST"; echo "removed $LABEL"; exit 0;;
  --observe) MODE="--observe";;
  --paper) MODE="";;
  *) echo "usage: $0 --observe | --paper | --remove"; exit 2;;
esac
[[ -x "$REPO/.venv/bin/python" ]] || { echo "missing $REPO/.venv"; exit 1; }
[[ -f "$REPO/data/telegram.session" ]] || { echo "no Telegram session; run scripts/telegram_login.py first"; exit 1; }
mkdir -p "$REPO/reports/signals_live" "$HOME/Library/LaunchAgents"
sed -e "s#__REPO__#$REPO#g" -e "s#__MODE__#$MODE#g" "$REPO/deploy/launchd/sgt.signals-live.plist.template" > "$DEST"
plutil -lint "$DEST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "installed $LABEL (${MODE:-paper trading}); logs: $REPO/reports/signals_live/"
