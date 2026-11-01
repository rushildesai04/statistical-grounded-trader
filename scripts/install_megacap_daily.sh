#!/usr/bin/env bash
# Install / remove the forward-test catch-up job (login/wake + daily 08:00, 12:00, 17:30, 21:00 local).
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
[[ -x "$REPO/.venv/bin/python" ]] || { echo "missing $REPO/.venv"; exit 1; }
mkdir -p "$REPO/reports/megacap_forward" "$REPO/reports/forward" "$HOME/Library/LaunchAgents"
IV=""
for hm in "8 0" "12 0" "17 30" "21 0"; do
  set -- $hm
  IV+="    <dict><key>Hour</key><integer>$1</integer><key>Minute</key><integer>$2</integer></dict>"$'\n'
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
echo "installed $LABEL (login/wake + daily 08:00 12:00 17:30 21:00); logs: reports/megacap_forward/run.log, reports/forward/run.log"
