#!/bin/zsh
# Daily data accumulation, run by launchd (see docs/runbooks/intraday_operations.md).
# Captures in-play symbols' 1-minute sessions + float snapshots after the post-market close.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
mkdir -p reports
STAMP="$(date +%Y-%m-%d)"
exec "$REPO/.venv/bin/python" scripts/capture_daily.py \
  --watchlist WFF,YMAT,WBUY,NCPL,SMTK,AAOI,FCHL,MSGY \
  --lookback-sessions 5 \
  --report-out "reports/accumulation_${STAMP}.md"
