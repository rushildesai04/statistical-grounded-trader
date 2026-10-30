#!/bin/zsh
# Install / remove the monthly passive-rebalance schedule (PAPER) as a launchd user agent.
# Works from any directory: the repo path is derived from this script's location.
#   scripts/install_schedule.sh            install (or reinstall)
#   scripts/install_schedule.sh --dry-run  print the rendered plist, change nothing
#   scripts/install_schedule.sh --remove   unload and delete the schedule
set -euo pipefail
LABEL="local.sgt.rebalance-passive"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO/deploy/launchd/sgt.rebalance-passive.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
CAP="${SGT_MAX_ORDER_NOTIONAL:-75000}"
DOMAIN="gui/$(id -u)"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$TARGET"
  echo "removed $LABEL"
  exit 0
fi

# Hourly 10:05-15:05 America/New_York on weekdays, expressed in this Mac's local time.
INTERVALS=""
for H in 10 11 12 13 14 15; do
  LOCAL=$(TZ=America/New_York date -j -f "%Y-%m-%d %H:%M" "$(TZ=America/New_York date +%Y-%m-%d) $H:05" +%s)
  LH=$(date -r "$LOCAL" +%-H); LM=$(date -r "$LOCAL" +%-M)
  for WD in 1 2 3 4 5; do
    INTERVALS+="    <dict><key>Weekday</key><integer>$WD</integer><key>Hour</key><integer>$LH</integer><key>Minute</key><integer>$LM</integer></dict>"$'\n'
  done
done
RENDERED=$(sed -e "s|__REPO__|$REPO|g" -e "s|__CAP__|$CAP|g" "$TEMPLATE")
RENDERED="${RENDERED/__INTERVALS__/${INTERVALS%$'\n'}}"
if [[ "${1:-}" == "--dry-run" ]]; then
  print -r -- "$RENDERED"
  echo "\n(would install to $TARGET; runs weekdays hourly 10:05-15:05 ET and at login)"
  exit 0
fi

case "$REPO" in
  "$HOME/Downloads"/*|"$HOME/Desktop"/*|"$HOME/Documents"/*)
    if [[ "${1:-}" != "--protected-folder-ok" ]]; then
      echo "REFUSING: the repo is at $REPO."
      echo "macOS blocks background (launchd) jobs from Downloads/Desktop/Documents unless the shell"
      echo "has Full Disk Access. Either:"
      echo "  a) grant /bin/zsh Full Disk Access (System Settings > Privacy & Security > Full Disk"
      echo "     Access > + > Cmd+Shift+G > /bin/zsh), then rerun with --protected-folder-ok"
      echo "  b) move the repo outside those folders and rerun"
      exit 1
    fi
    echo "note: installing from a protected folder; requires /bin/zsh Full Disk Access";;
esac
[[ -x "$REPO/.venv/bin/python" ]] || { echo "missing $REPO/.venv (create the virtualenv first)"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/reports/rebalance"
print -r -- "$RENDERED" > "$TARGET"
plutil -lint "$TARGET" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET"
echo "installed $LABEL -> $TARGET"
echo "runs weekdays hourly 10:05-15:05 ET and at login; trades once per month within the first 5 sessions"
echo "log: $REPO/reports/rebalance/run.log"
launchctl print "$DOMAIN/$LABEL" | grep -E "state|path" | head -3
