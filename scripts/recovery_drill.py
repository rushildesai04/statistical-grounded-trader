"""Controlled recovery drill (owner 2026-11-23: "Run a controlled recovery test. Use an isolated test channel or
harness to simulate alerts arriving during downtime ... Do not wait for a production incident or generate real orders").

    python scripts/recovery_drill.py run [--dir DIR] [--speed 12]

What it is. A listener process built from the SAME parts the live listener uses (the channel's real message format
and parser ``fade_live.to_alerts``, ``alert_recovery.catch_up`` / ``pushed`` / ``dispatch``, ``fade.on_alert``,
``fade.monitor``) runs against an isolated channel (a file), an isolated price tape (a file) and its own empty
database in a temporary directory. The drill KILLS that process (SIGKILL, no clean-up), posts alerts while it is
down, starts it again and checks what the database then says. Texts go to a file. Nothing here can reach Telegram,
a broker, a phone or the live database: there is no network call in it and its working directory is the temporary one.

What it is not. Telegram's own delivery is not exercised (how the library pages a channel was read from its source;
the first production proof is the first deploy after the cursor exists). The clock runs ``--speed`` times faster
than real time, so "five minutes" takes 25 seconds; every rule reads the drill's clock, as the tests' rules read
theirs. The long book is not in the drill (its restart rule has five deterministic cases in the tests).

Exit code 0 only when every check passes. The result table is the evidence quoted in
docs/findings/recovery_verification_2026-11-23.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BASE = datetime(2026, 11, 23, 10, 0, 40, tzinfo=ET).astimezone(timezone.utc)   # a session morning, on the drill's clock
TICK = 0.2                                   # real seconds between the child's passes


def _clock(d: Path):
    cfg = json.loads((d / "config.json").read_text())
    return lambda: BASE + timedelta(seconds=(time.time() - cfg["t0"]) * cfg["speed"])


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


# ---- the listener process ---------------------------------------------------------------------------------------

def listen(d: Path) -> int:
    import fade_live as fl
    from sgt.fast import alert_recovery as ar
    from sgt.fast import fade
    from sgt.ops.store import OpsStore

    now, db = _clock(d), OpsStore(d / "reports" / "ops" / "ops.db")
    state = ar.State(str(fl.CHANNEL), now())

    def message(r: dict):
        return SimpleNamespace(id=r["id"], message=r["text"], date=datetime.fromisoformat(r["posted"]))

    def channel() -> list:
        if (d / "channel.down").exists():
            raise OSError("the drill's channel is unreachable")
        return [message(r) for r in _lines(d / "channel.jsonl")]

    async def fetch(after: int, limit: int):
        return [m for m in sorted(channel(), key=lambda m: m.id) if m.id > after][:limit]

    async def newest():
        return max((m.id for m in channel()), default=0)

    async def before(t: datetime):
        return max((m.id for m in channel() if m.date < t), default=None)

    def handle(msg, how: str):
        return ar.dispatch(db, msg, how, fl.to_alerts, now=now)

    def quote(symbol: str, since: datetime | None = None):
        try:
            px, at = json.loads((d / "tape.json").read_text()).get(symbol) or (None, None)
        except (OSError, ValueError):
            return None
        return None if px is None or (since is not None and datetime.fromisoformat(at) < since) else px
    quote.since_aware = True

    def send(text: str) -> None:
        with (d / "texts.jsonl").open("a") as f:
            f.write(json.dumps({"at": now().isoformat(), "text": text[:160]}) + "\n")

    async def channel_loop():
        while True:
            try:
                await ar.catch_up(db, state, fetch, handle, newest, before, now=now)
            except Exception as exc:  # noqa: BLE001 - as the live loop: logged, tried again
                print("channel check failed:", type(exc).__name__, flush=True)
            await asyncio.sleep(TICK)

    async def push_loop():
        take, seen = ar.pushed(state, handle), 0
        while True:
            rows = _lines(d / "push.jsonl")
            for r in rows[seen:]:
                take(message(r))
            seen = len(rows)
            await asyncio.sleep(TICK / 4)

    async def monitor_loop():
        n = 0
        while True:
            await asyncio.to_thread(fade.monitor, db, send, quote, now(), None, None, None, None, state.up_since)
            n += 1
            (d / f"ticks.{os.getpid()}").write_text(str(n))
            await asyncio.sleep(TICK)

    async def main():
        await asyncio.gather(channel_loop(), push_loop(), monitor_loop())

    asyncio.run(main())
    return 0


# ---- the drill ------------------------------------------------------------------------------------------------------

class Drill:
    def __init__(self, d: Path, speed: float) -> None:
        self.d, self.results, self.child = d, [], None
        (d / "reports" / "ops").mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({"t0": time.time(), "speed": speed}))
        self.now = _clock(d)
        self.db = d / "reports" / "ops" / "ops.db"

    def q(self, sql: str, *args) -> list[dict]:
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(sql, args)]
        finally:
            c.close()

    def row(self, symbol: str) -> dict | None:
        r = self.q("SELECT * FROM fade_trades WHERE symbol=? ORDER BY id", symbol)
        return r[-1] if r else None

    def start(self) -> None:
        self.child = subprocess.Popen([sys.executable, "-W", "ignore", __file__, "listen", "--dir", str(self.d)],
                                      cwd=self.d, stdout=(self.d / "child.log").open("a"), stderr=subprocess.STDOUT)

    def kill(self) -> None:
        os.kill(self.child.pid, signal.SIGKILL)             # no clean-up of any kind: a crash
        self.child.wait()

    def ticks(self) -> int:
        p = self.d / f"ticks.{self.child.pid}"
        return int(p.read_text() or 0) if p.exists() else 0

    def wait(self, what: str, cond, timeout: float = 30.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.child is not None and self.child.poll() is not None:
                raise SystemExit(f"the listener process died while waiting for: {what} (see {self.d / 'child.log'})")
            try:
                got = cond()
            except sqlite3.Error:
                got = None
            if got:
                return got
            time.sleep(0.05)
        raise SystemExit(f"timed out waiting for: {what}")

    def post(self, mid: int, text: str, posted: datetime, push: bool = False) -> None:
        line = json.dumps({"id": mid, "text": text, "posted": posted.isoformat()}) + "\n"
        with (self.d / "channel.jsonl").open("a") as f:
            f.write(line)
        if push:
            with (self.d / "push.jsonl").open("a") as f:
                f.write(line)

    def print_at(self, symbol: str, px: float) -> datetime:
        p, at = self.d / "tape.json", self.now()
        tape = json.loads(p.read_text()) if p.exists() else {}
        tape[symbol] = [px, at.isoformat()]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(tape))
        tmp.replace(p)
        return at

    def fresh(self) -> datetime:
        """A posting time whose tested entry minute has already begun: 15 s before the current minute's start."""
        return self.now().replace(second=0, microsecond=0) - timedelta(seconds=15)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, bool(ok), detail))
        print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""), flush=True)

    def texts(self) -> int:
        return len(_lines(self.d / "texts.jsonl"))


def run(d: Path, speed: float) -> int:
    from sgt.ops import approvals
    from sgt.ops.store import OpsStore

    from sgt.fast import fade

    entries = getattr(fade, "RECOVERED_SHORT_ENTRIES", True)        # False = record only (the drill checks that instead)
    print(f"recovered short alerts may enter: {entries}", flush=True)
    dr = Drill(d, speed)
    approvals.arm(OpsStore(dr.db), dr.now())
    dr.post(100, "ZZZO halted 9.00", dr.now() - timedelta(minutes=30))
    ev = lambda mid: dr.q("SELECT * FROM alert_events WHERE message_id=? ORDER BY symbol", mid)  # noqa: E731
    cursor = lambda: (dr.q("SELECT value FROM flags WHERE key LIKE 'alert_cursor:%'") or [{"value": None}])[0]["value"]  # noqa: E731

    # 1. a listener is running and takes a short on a live alert
    dr.start()
    dr.wait("the first start to set the cursor", lambda: cursor() == "100")
    dr.post(101, "ZZZA  volume increasing  5.00", dr.fresh(), push=True)
    dr.wait("the live alert's row", lambda: dr.row("ZZZA"))
    dr.print_at("ZZZA", 5.00)
    dr.wait("the live short to open", lambda: (dr.row("ZZZA") or {}).get("status") == "open")
    dr.wait("its text", lambda: dr.texts() >= 1, 10)
    a = dr.row("ZZZA")
    dr.check("live alert: handled by push, entered at the current print, one text",
             (a["via"], a["est_entry"], dr.texts()) == ("push", 5.0, 1), f"via {a['via']}, entry {a['est_entry']}")

    # 2. the process is killed; alerts are posted while it is down
    dr.kill()
    killed = dr.now()
    dr.post(102, "ZZZB  volume increasing  3.00", dr.now() - timedelta(minutes=6))           # stale by the time it is back
    dr.post(103, "ZZZA new high 5.50", dr.now() - timedelta(seconds=30))                     # the stock already has a row
    dr.post(104, "ZZZC  gaining volume  4.00\nZZZD new high 7.00", dr.fresh())               # two stocks, still fresh
    old = dr.print_at("ZZZC", 4.00)                                                          # a print DURING the downtime
    time.sleep(1.0)

    # 3. it comes back
    dr.start()
    dr.wait("the catch-up to reach the newest message", lambda: cursor() == "104")
    dr.wait("the listener to have looked at the prices at least three times", lambda: dr.ticks() >= 3)
    b, c, dd = dr.row("ZZZB"), dr.row("ZZZC"), dr.row("ZZZD")
    waiting = "pending" if entries else "skipped"           # record only: a recovered alert never waits to enter
    order = [r["message_id"] for r in dr.q("SELECT message_id FROM alert_events WHERE message_id > 101 ORDER BY rowid")]
    dr.check("ordering: the downtime's messages were handled oldest first", order == [102, 103, 104, 104], str(order))
    lab = dr.q("SELECT symbol, via, posted_at, recovered_at FROM alert_events WHERE message_id > 101")
    dr.check("replay label: every recovered alert carries via=replay, its posting time and a later recovery time",
             len(lab) == 4 and all(r["via"] == "replay" and r["recovered_at"] > r["posted_at"]
                                   and datetime.fromisoformat(r["recovered_at"]) > killed for r in lab))
    dr.check("expiration: the alert older than five minutes is recorded and not entered",
             (b["status"], b["reason"], b["est_entry"], b["via"]) == ("skipped", "alert received late", None, "replay"),
             f"{b['status']}: {b['reason']}")
    dr.check("one trade per stock survives the crash: the second alert of the shorted stock is a duplicate, no row",
             [r["status"] for r in ev(103)] == ["duplicate"] and len(dr.q("SELECT 1 FROM fade_trades WHERE symbol='ZZZA'")) == 1)
    dr.check("current price: a fresh recovered alert is NOT entered on the print from the downtime",
             (c["status"], c["est_entry"]) == (waiting, None) and datetime.fromisoformat(c["recovered_at"]) > old,
             f"print at {old:%H:%M:%S}, recovered {c['recovered_at'][11:19]}, status {c['status']}")
    n = dr.texts()
    new = dr.print_at("ZZZC", 4.20)
    if entries:
        dr.wait("the recovered short to open on the new print", lambda: (dr.row("ZZZC") or {}).get("status") == "open")
        dr.wait("its text (sent right after the entry is booked)", lambda: dr.texts() > n, 10)
        c = dr.row("ZZZC")
        dr.check("current price: it enters on the first print AFTER the recovery, at that price, one text",
                 c["est_entry"] == 4.20 and datetime.fromisoformat(c["est_entry_at"]) >= new and dr.texts() == n + 1,
                 f"entry {c['est_entry']} at {c['est_entry_at'][11:19]}")
        dr.check("both timelines kept: posting time, recovery time and entry time are three different stored times",
                 c["posted_at"] < c["recovered_at"] < c["est_entry_at"])
    else:
        t = dr.ticks()
        dr.wait("the listener to look at the new print several times", lambda: dr.ticks() >= t + 4)
        c = dr.row("ZZZC")
        dr.check("record only: with a fresh print after the recovery the recovered alert is still not entered, no text",
                 (c["status"], c["est_entry"]) == ("skipped", None) and dr.texts() == n, f"{c['status']}: {c['reason']}")
        dr.check("both timelines kept: posting time and recovery time are stored on the recorded alert",
                 c["posted_at"] < c["recovered_at"])

    # 4. the channel becomes unreachable with shorts open: exits must not wait for it
    (d / "channel.down").write_text("x")
    n = dr.texts()
    dr.print_at("ZZZA", 6.10)                                                                # through the 20% stop
    dr.wait("the stop exit while the channel is unreachable", lambda: (dr.row("ZZZA") or {}).get("status") == "est_closed", 10)
    dr.wait("its text (sent right after the exit is booked)", lambda: dr.texts() > n, 10)
    dr.check("position protection: the stop exit is booked and texted while the alert channel is unreachable",
             dr.row("ZZZA")["est_exit"] == 6.10 and dr.texts() == n + 1 and (d / "channel.down").exists())

    # 5. the fresh alert whose stock never prints expires
    dr.wait("the five minutes of the alert that never printed to pass",
            lambda: (dr.row("ZZZD") or {}).get("status") == "skipped", 300 / speed + 20)
    dd = dr.row("ZZZD")
    dr.check("expiration: a recovered alert whose stock does not print inside five minutes is skipped, never entered",
             (dd["status"], dd["est_entry"]) == ("skipped", None), dd["reason"])

    # 6. a crash after the alerts were stored but before the cursor moved; then a second delivery by push
    dr.kill()
    before = (len(dr.q("SELECT 1 FROM fade_trades")), len(dr.q("SELECT 1 FROM alert_events")),
              len(dr.q("SELECT 1 FROM decisions")), dr.texts())
    w = sqlite3.connect(dr.db, timeout=10)
    w.execute("UPDATE flags SET value='101' WHERE key LIKE 'alert_cursor:%'")                # as if it died before moving it
    w.commit()
    w.close()
    (d / "channel.down").unlink()
    dr.start()
    dr.wait("the third start to re-read the downtime's messages", lambda: cursor() == "104")
    with (d / "push.jsonl").open("a") as f:                                                  # and the same message pushed again
        f.write(json.dumps({"id": 104, "text": "ZZZC  gaining volume  4.00\nZZZD new high 7.00",
                            "posted": dr.row("ZZZC")["posted_at"]}) + "\n")
    t = dr.ticks()
    dr.wait("a few more passes", lambda: dr.ticks() >= t + 4)
    after = (len(dr.q("SELECT 1 FROM fade_trades")), len(dr.q("SELECT 1 FROM alert_events")),
             len(dr.q("SELECT 1 FROM decisions")), dr.texts())
    dr.check("deduplication: re-reading and re-pushing the same alerts adds no row, identifier, decision line or text",
             before == after, f"rows, identifiers, decision lines, texts: {before} -> {after}")
    c2 = dr.row("ZZZC")
    if entries:
        dr.check("position state survives two crashes: the recovered short is still open at its entry",
                 (c2["status"], c2["est_entry"], c2["recovered_at"]) == ("open", 4.20, c["recovered_at"]))
    else:
        dr.check("state survives two crashes: the recovered alert is still recorded only, the live short's exit stands",
                 (c2["status"], c2["est_entry"], c2["recovered_at"]) == ("skipped", None, c["recovered_at"])
                 and dr.row("ZZZA")["status"] == "est_closed")
    dr.kill()
    gaps = dr.q("SELECT reason FROM alert_gaps")
    print(f"\ngap rows: {[g['reason'][:60] for g in gaps]}")
    print(f"directory: {d}")
    bad = [n for n, ok, _ in dr.results if not ok]
    print(f"\n{len(dr.results) - len(bad)} of {len(dr.results)} checks passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("run", "listen"))
    ap.add_argument("--dir", default=None)
    ap.add_argument("--speed", type=float, default=12.0)
    args = ap.parse_args()
    if args.cmd == "listen":
        return listen(Path(args.dir))
    d = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="recovery_drill_"))
    if any(d.iterdir()) if d.exists() else False:
        raise SystemExit(f"{d} is not empty: the drill only runs in a directory of its own")
    d.mkdir(parents=True, exist_ok=True)
    os.chdir(d)
    return run(d, args.speed)


if __name__ == "__main__":
    raise SystemExit(main())
