#!/usr/bin/env bash
# Install / remove the forward-test job (weekdays 08:30 and 15:50 US Eastern).
#   scripts/install_megacap_daily.sh            # install
#   scripts/install_megacap_daily.sh --remove
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=local.sgt.megacap-daily
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$DEST"; echo "removed $LABEL"; exit 0
fi
TZNAME="$(readlink /etc/localtime | sed 's#.*zoneinfo/##')"
[[ "$TZNAME" == "America/New_York" ]] || { echo "REFUSING: Mac timezone is $TZNAME, job times assume America/New_York"; exit 1; }
[[ -x "$REPO/.venv/bin/python" ]] || { echo "missing $REPO/.venv"; exit 1; }
mkdir -p "$REPO/reports/megacap_forward" "$HOME/Library/LaunchAgents"
IV=""
for wd in 1 2 3 4 5; do
  for hm in "8 30" "15 50"; do
    set -- $hm
    IV+="    <dict><key>Weekday</key><integer>$wd</integer><key>Hour</key><integer>$1</integer><key>Minute</key><integer>$2</integer></dict>"$'\n'
  done
done
python3 - "$REPO" "$DEST" "$IV" <<'PY'
import sys, pathlib
repo, dest, iv = sys.argv[1], sys.argv[2], sys.argv[3]
t = pathlib.Path(repo, "deploy/launchd/sgt.megacap-daily.plist.template").read_text()
pathlib.Path(dest).write_text(t.replace("__REPO__", repo).replace("__INTERVALS__", iv.rstrip("\n")))
PY
plutil -lint "$DEST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "installed $LABEL (weekdays 08:30 and 15:50 ET); log: $REPO/reports/megacap_forward/run.log"
