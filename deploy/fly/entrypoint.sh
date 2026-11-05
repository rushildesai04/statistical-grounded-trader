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
mkdir -p reports/megacap_forward reports/forward reports/rebalance reports/ops reports/fast reports/alert_fade reports/fade_challengers reports/fade_exits reports/fade_hold data/alpaca data/etf_yf data/borrow

run() {  # run <log> <cmd...>: timestamped, exit code recorded, never silent
  log="$1"; shift
  echo "=== $(date '+%F %T %Z') $*" >> "$log"
  if "$@" >> "$log" 2>&1; then rc=0; else rc=$?; fi
  if [ "$rc" -ne 0 ]; then
    echo "!!! exit $rc" >> "$log"
    python /app/scripts/ops_notify.py "${JOB:-job}" --log "$log" \
      >> "$log" 2>&1 || echo "!!! alert failed" >> "$log"
  fi
  return "$rc"
}

if [ "${1:-}" = "job" ]; then
  JOB="${2:-}"                                  # names the job in plain-English failure texts (ops_notify)
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
    # Plan 2026-11-04: IBKR borrow snapshots (W1) and the decisive forward research record on the runner (W2).
    borrow)    run reports/fast/borrow.log python /app/scripts/fade_live.py borrow ;;
    fadeforward) run reports/alert_fade/run.log python /app/scripts/alert_fade_forward.py --no-telegram ;;
    fadechallengers) run reports/fade_challengers/run.log python /app/scripts/fade_challengers.py shadow ;;
    fadeexits) run reports/fade_exits/run.log python /app/scripts/fade_exits.py shadow ;;
    fadehold)  run reports/fade_hold/run.log python /app/scripts/fade_exits.py shadow --study hold ;;
    # Live pilot (2026-11-05): real 60/40 + turn-of-month at Schwab; a no-op unless SGT_PILOT_SCHWAB=1.
    pilotbook) run reports/ops/pilot.log python /app/scripts/sms_control.py pilot ;;
    # Fade Tape chart page (2026-11-05): rebuilt from the ops DB + SIP bars; served by sgt.ops.chartserver.
    charts)    run reports/charts/run.log python /app/scripts/trade_charts.py --out reports/charts/fade_tape.html ;;
    # Midnight day summary (owner request 2026-11-05): trades, count, $ made/lost, $ traded.
    summary)   run reports/ops/run.log python /app/scripts/sms_control.py summary ;;
    *) echo "unknown job ${2:-}" >&2; exit 2 ;;
  esac
  exit $?
fi

# Boot: catch up anything missed while the machine was down, then hand over to cron.
"$0" job megacap || true
# Fast alert-fade listener (2026-11-03): only with its own Telegram session secret; restarted on exit.
# Health shows in the digest (heartbeat), not pages, so a Telegram outage cannot flood SMS.
if [ -n "${SGT_SECRET_TG_FADE_SESSION:-}" ]; then
  ( while true; do
      python /app/scripts/fade_live.py run >> reports/fast/listener.log 2>&1 || true
      echo "=== $(date '+%F %T %Z') listener exited; restart in 30s" >> reports/fast/listener.log
      sleep 30
    done ) &
fi
# Fade Tape server (2026-11-05): only with a valid secret link token (>= 32 chars); restarted on exit. It runs with
# a clean environment holding ONLY its token, never the trading secrets (security review L2/L4).
mkdir -p reports/charts
CHARTS_TOKEN="${SGT_SECRET_CHARTS_TOKEN:-}"
if [ "${#CHARTS_TOKEN}" -ge 32 ]; then
  ( while true; do
      env -i PATH="$PATH" PYTHONPATH="${PYTHONPATH:-}" HOME="${HOME:-/tmp}" LANG=C.UTF-8 \
        PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
        SGT_SECRET_CHARTS_TOKEN="$CHARTS_TOKEN" \
        python -m sgt.ops.chartserver >> reports/charts/server.log 2>&1 || true
      echo "=== $(date '+%F %T %Z') chart server exited; restart in 10s" >> reports/charts/server.log
      sleep 10
    done ) &
  "$0" job charts || true
fi
exec supercronic -passthrough-logs /app/deploy/crontab
