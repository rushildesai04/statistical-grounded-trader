#!/bin/sh
# Entry point for the Fly runner. `entrypoint.sh` = boot (catch-up once, then cron);
# `entrypoint.sh job <name>` = one scheduled job, output appended to its log on the volume.
set -eu
# Fly mounts the volume root-owned: fix ownership once, then re-exec unprivileged (uid 10001).
if [ "$(id -u)" = "0" ]; then
  mkdir -p "${SGT_HOME:?}" && chown -R sgt:sgt "$SGT_HOME"
  exec setpriv --reuid=sgt --regid=sgt --init-groups "$0" "$@"
fi
cd "${SGT_HOME:?}"
mkdir -p reports/megacap_forward reports/forward reports/rebalance data/alpaca data/etf_yf

run() {  # run <log> <cmd...>: timestamped, exit code recorded, never silent
  log="$1"; shift
  echo "=== $(date '+%F %T %Z') $*" >> "$log"
  if "$@" >> "$log" 2>&1; then rc=0; else rc=$?; fi
  [ "$rc" -eq 0 ] || echo "!!! exit $rc" >> "$log"
  return "$rc"
}

if [ "${1:-}" = "job" ]; then
  case "${2:-}" in
    megacap)   run reports/megacap_forward/run.log python /app/scripts/megacap_forward.py auto ;;
    etf)       run reports/forward/run.log python /app/scripts/forward_paper.py refresh || true
               run reports/forward/run.log python /app/scripts/forward_paper.py snapshot ;;
    rebalance) run reports/rebalance/run.log python /app/scripts/rebalance_passive.py --execute \
                 --max-order-notional "${SGT_MAX_ORDER_NOTIONAL:-75000}" ;;
    *) echo "unknown job ${2:-}" >&2; exit 2 ;;
  esac
  exit $?
fi

# Boot: catch up anything missed while the machine was down, then hand over to cron.
"$0" job megacap || true
exec supercronic -passthrough-logs /app/deploy/crontab
