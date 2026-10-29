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

# 15:40 America/New_York expressed in this Mac's local time (launchd uses local time).
LOCAL=$(TZ=America/New_York date -j -f "%Y-%m-%d %H:%M" "$(TZ=America/New_York date +%Y-%m-%d) 15:40" +%s)
HOUR=$(date -r "$LOCAL" +%-H); MINUTE=$(date -r "$LOCAL" +%-M)

RENDERED=$(sed -e "s|__REPO__|$REPO|g" -e "s|__CAP__|$CAP|g" -e "s|__HOUR__|$HOUR|g" \
               -e "s|__MINUTE__|$MINUTE|g" "$TEMPLATE")
if [[ "${1:-}" == "--dry-run" ]]; then
  print -r -- "$RENDERED"
  echo "\n(would install to $TARGET; runs weekdays at $HOUR:$(printf %02d $MINUTE) local)"
  exit 0
fi

case "$REPO" in
  "$HOME/Downloads"/*|"$HOME/Desktop"/*|"$HOME/Documents"/*)
    echo "REFUSING: the repo is at $REPO."
    echo "macOS privacy rules block background (launchd) jobs from reading Downloads/Desktop/Documents,"
    echo "so the job would load but fail every run. Move the repo first, e.g.:"
    echo "    mkdir -p ~/code && mv \"$REPO\" ~/code/"
    echo "then run ~/code/$(basename "$REPO")/scripts/install_schedule.sh"
    exit 1;;
esac
[[ -x "$REPO/.venv/bin/python" ]] || { echo "missing $REPO/.venv (create the virtualenv first)"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/reports/rebalance"
print -r -- "$RENDERED" > "$TARGET"
plutil -lint "$TARGET" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET"
echo "installed $LABEL -> $TARGET"
echo "runs weekdays at $HOUR:$(printf %02d $MINUTE) local (15:40 ET); acts only on the first trading day of the month"
echo "log: $REPO/reports/rebalance/run.log"
launchctl print "$DOMAIN/$LABEL" | grep -E "state|path" | head -3
