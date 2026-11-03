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
mkdir -p reports/megacap_forward reports/forward reports/rebalance reports/ops reports/fast data/alpaca data/etf_yf

run() {  # run <log> <cmd...>: timestamped, exit code recorded, never silent
  log="$1"; shift
  echo "=== $(date '+%F %T %Z') $*" >> "$log"
  if "$@" >> "$log" 2>&1; then rc=0; else rc=$?; fi
  if [ "$rc" -ne 0 ]; then
    echo "!!! exit $rc" >> "$log"
    python /app/scripts/ops_notify.py "$(basename "$(dirname "$log")") job failed (exit $rc)" --log "$log" \
      >> "$log" 2>&1 || echo "!!! alert failed" >> "$log"
  fi
  return "$rc"
}

if [ "${1:-}" = "job" ]; then
  case "${2:-}" in
    megacap)   run reports/megacap_forward/run.log python /app/scripts/megacap_forward.py auto
               run reports/ops/run.log python /app/scripts/sms_control.py megacap ;;
    etf)       run reports/forward/run.log python /app/scripts/forward_paper.py refresh || true
               run reports/forward/run.log python /app/scripts/forward_paper.py snapshot
               run reports/forward/run.log python /app/scripts/forward_paper.py evaluate ;;
    rebalance) run reports/rebalance/run.log python /app/scripts/rebalance_passive.py --execute \
                 --max-order-notional "${SGT_MAX_ORDER_NOTIONAL:-75000}" ;;
    # SMS control (2026-11-02). The per-minute poll logs only when it acts or fails, and never pages
    # (a Twilio outage would page every minute); failures surface in the daily digest instead.
    sms)       python /app/scripts/sms_control.py poll >> reports/ops/poll.log 2>&1 || \
                 { echo "=== $(date '+%F %T %Z') poll"; echo "!!! exit"; } >> reports/ops/poll.log ;;
    tom)       run reports/ops/run.log python /app/scripts/sms_control.py tom ;;
    etfbook)   run reports/ops/run.log python /app/scripts/sms_control.py etf ;;
    digest)    run reports/ops/run.log python /app/scripts/sms_control.py digest ;;
    # Fast alert-fade book (2026-11-03): official results from SIP bars before the digest.
    fadesettle) run reports/fast/run.log python /app/scripts/fade_live.py settle ;;
    *) echo "unknown job ${2:-}" >&2; exit 2 ;;
  esac
  exit $?
fi

# Boot: catch up anything missed while the machine was down, then hand over to cron.
"$0" job megacap || true
# Fast alert-fade listener (2026-11-03): only with its dedicated Telegram session secret; restarted on exit.
# Health shows in the digest (heartbeat), not pages, so a Telegram outage cannot flood SMS.
if [ -n "${SGT_SECRET_TG_FADE_SESSION:-}" ]; then
  ( while true; do
      python /app/scripts/fade_live.py run >> reports/fast/listener.log 2>&1 || true
      echo "=== $(date '+%F %T %Z') listener exited; restart in 30s" >> reports/fast/listener.log
      sleep 30
    done ) &
fi
exec supercronic -passthrough-logs /app/deploy/crontab
