"""The after-the-session check of the feed recorder, and what follows a confirmed breach (Stage 0, section 4a of
docs/findings/proposal_realtime_feed_shadow_2026-11-27.md).

Used by the nightly job (``scripts/feed_shadow.py``), which stays the place where the day is compared and the line
is written. Here: the frozen tolerance, the reading of the long book's own cadence record (``pace``), the
output-integrity row (``integrity``), the three answers inside / outside / not measured (``verdict``), the list of
confirmed breaches with figure and limit (``breaches``), the switch that a confirmed breach makes (``shutoff``:
the recorder's OFF file, created whole and never over an existing one; evidence kept; nothing else touched), and
the one line for the midnight summary text (``report``).

Nothing here is read by a book, and nothing here writes to the operations database.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from sgt.fast import feed_shadow as fs
from sgt.ops import own_thread
from sgt.ops.own_thread import FLUSH_S

# The check of section 4a, made by the
# job itself so that it does not depend on a person reading two files (owner 2026-11-27: "Collect the first full
# session's recorder measurements against the predefined tolerances"). FROZEN on 2026-11-27, before the recorder had
# run on the runner (owner: "Do not tighten or loosen thresholds after seeing the recorder's results to manufacture a
# pass"): these four numbers are that section's table and change only on the owner's word. The figures come from the
# long book's own cadence record (sgt.ops.own_thread.Pace); nothing is measured anew and nothing is switched by it.
PACE, LOOP = Path("reports/ops/loop_gaps.jsonl"), "long book"
TOL = {"within_min": 0.995, "late_max": 0, "work_p90_max_s": 0.2, "work_max_max_s": 0.5}
LIMITS = ("baseline of two full sessions; the record keeps one figure per 10 minutes (the longest pass) to 0.1 s, so "
          "a cost under 0.1 s a pass cannot be seen; a figure inside the tolerance is not proof of no effect")
# Not a tolerance: whether the record is whole enough to say "inside" at all. The book writes one row every FLUSH_S
# (10 minutes); a stretch of the window twice that long without a row means at least one row is missing (a restart,
# a full record). Fixed 2026-11-27 with the four numbers, before the first session was read. A figure OUTSIDE its
# limit stays outside on a partial record (what was seen was seen); a figure inside becomes "not measured".
HOLE_S = 2 * FLUSH_S
# The check in the owner's words, for the one line that goes into the midnight summary text (sgt.ops.summary).
PLAIN = {"cadence": "passes on time", "late": "late passes", "work_p90": "longest pass (90th percentile)",
         "work_max": "longest pass (maximum)", "dropped_0": "looks dropped", "write_fails_0": "failed writes",
         "not_switched_off": "recorder switched off", "counts_add_up": "class counts"}
POLICY = ("A confirmed breach switches the recorder off (the nightly job creates its OFF file); only a person puts "
          "it back on")
# Owner 2026-11-27 (evening): "On a confirmed tolerance breach, atomically create the recorder's OFF file." A check
# that is False is a confirmed breach; one that is None (not measured) never switches anything, and a False one
# counts even when another is None. Only the recorder is stopped (its own watchdog sees the file within 2 s): no
# restart, no book, size, flag or registered figure is touched, and nothing here ever removes the file.
EVIDENCE = Path("reports/feed_shadow/evidence")
CONFIRM_S = 10.0
# Owner 2026-11-27 (night): a switch the running recorder does not acknowledge within CONFIRM_S is "unconfirmed",
# never a success, and goes out through the job-failure path like a file that could not be made. "Once per session"
# stops a second SUCCESSFUL switch, not a retry after a failed one: the file is tried ATTEMPTS times in one run, and
# a switch still failed or unconfirmed is taken up again by the following scheduled runs, MAX_NIGHTS in all. A file
# that was made is never made again: a person who removes it has put the recorder back on, and that stands.
ATTEMPTS, ATTEMPT_PAUSE_S, MAX_NIGHTS = 3, 2.0, 5
DONE, OPEN = ("created", "already present", "already off", "removed by a person"), ("failed", "unconfirmed")


def pace(session: str, path: Path = PACE) -> dict:
    """``_pace`` without the rows: the figures and verdicts of one session."""
    return _pace(session, path)[0]


def _pace(session: str, path: Path = PACE) -> tuple[dict, list[str]]:
    """Returns (the figures, the long book's raw rows of the window: the evidence kept when a figure is outside).
    ``path`` names the old single file; the day files are found next to it (``own_thread.files``).

    The long book's own cadence record over the session's regular hours, set against the frozen tolerance (TOL).
    The window is the baseline read's: rows stamped from 10 minutes after the open (a row is written at the END of
    its 10 minutes) to the minute of the close. Never raises: a record that cannot be read is said in ``note`` and
    every verdict stays None (not measured), never True. A record with a row missing from the window (HOLE_S) or
    with a line that cannot be read is ``whole: False``: an outside figure stays outside, an inside one is None."""
    first, close = fs.session_minutes(session)
    lo, hi = first + timedelta(minutes=10), close + timedelta(seconds=59)
    out = {"loop": LOOP, "window_et": f"{lo.tz_convert(fs.ET):%H:%M}-{close.tz_convert(fs.ET):%H:%M}",
           "full_session": close - first == timedelta(hours=6, minutes=30), "tolerance": TOL, "limits": LIMITS,
           "inside": {"cadence": None, "late": None, "work_p90": None, "work_max": None}}
    raw, found = [], 0                                  # the old single file, then the day's own file: time order
    for f in own_thread.files(Path(path).parent, {lo.date(), hi.date()}) if Path(path).name == own_thread.GAPS_NAME else [Path(path)]:
        try:
            raw += f.read_text(encoding="utf-8").splitlines()
            found += 1
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:              # ValueError: bytes that are not text
            return ({**out, "note": f"the cadence record could not be read ({type(e).__name__})"}, [])
    if not found:
        return ({**out, "note": "the cadence record could not be read (FileNotFoundError)"}, [])
    full = any(own_thread.capped(Path(path).parent, d.isoformat()) for d in {lo.date(), hi.date()})
    stopped = any(own_thread.capped(Path(path).parent, d.isoformat(), hard=True) for d in {lo.date(), hi.date()})
    n, counts, work, late, stamps, rows = 0, [0, 0, 0, 0], [], 0, [], []
    unread, pending, prev = 0, 0, None      # a line that cannot be read counts unless its neighbours place it outside
    for ln in raw:
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
            at = pd.Timestamp(r["at"])
            if at.tzinfo is None:                       # NaT too: a stamp that cannot be placed
                raise ValueError("stamp")
        except (ValueError, TypeError, KeyError, AttributeError):
            pending += 1
            continue
        if pending:                                     # the file is written in time order
            unread += 0 if (at < lo or (prev is not None and prev > hi)) else pending
            pending = 0
        prev = at
        if r.get("loop") != LOOP or not lo <= at <= hi:
            continue
        try:
            if r.get("kind") == "summary":
                c = [int(x) for x in r["counts"]]
                if len(c) != 4:
                    raise ValueError("counts")
                n, counts, work = n + int(r["n"]), [x + y for x, y in zip(counts, c)], work + [float(r["max_work"])]
                stamps.append(at)
                rows.append(ln)
            elif "gap" in r:
                late += 1                               # a late pass has a line of its own
                rows.append(ln)
        except (ValueError, TypeError, KeyError):
            unread += 1
    if pending and not (prev is not None and prev > hi):
        unread += pending
    edges = [lo] + sorted(stamps) + [hi]
    hole = max((b - a).total_seconds() for a, b in zip(edges, edges[1:]))
    out.update(passes=n, counts=counts, late=late, periods=len(work), unreadable_lines=unread,
               longest_stretch_without_a_row_s=round(hole), whole=hole < HOLE_S and not unread and not stopped)
    if not n:
        return ({**out, "note": "no cadence row of the long book in the session's regular hours"}, rows)
    w = sorted(work)
    share, p90, top = counts[0] / n, w[int(0.9 * (len(w) - 1))], w[-1]     # the 90th percentile as the baseline took it
    out.update(within=round(share, 5), work_p90_s=p90, work_max_s=top)
    seen = {"cadence": share >= TOL["within_min"], "late": late <= TOL["late_max"],
            "work_p90": p90 <= TOL["work_p90_max_s"], "work_max": top <= TOL["work_max_max_s"]}
    if not out["whole"]:
        why = ([f"longest stretch of the window without a row {hole / 60:.0f} min"] if hole >= HOLE_S else []) + (
            [f"{unread} lines that cannot be read and may belong to the window"] if unread else []) + (
            ["the day's record reached the size at which no row is written any more"] if stopped else [])
        out["incomplete"] = "the cadence record is not whole (" + "; ".join(why) + "): a figure inside its limit is not measured"
        seen = {k: (False if v is False else None) for k, v in seen.items()}
    if full and seen["late"] is True:                   # the day's file reached the size where late-pass rows stop
        seen["late"] = None
        out["incomplete"] = (out.get("incomplete", "") + "; " if out.get("incomplete") else "") + (
            "the day's record reached its size limit: late-pass rows may be missing, so no late pass is not measured")
    out["inside"] = seen
    return out, rows


def integrity(o: dict) -> dict:
    """Section 4a's output-integrity row from the line itself: True, False, or None where it was not measured."""
    h = o.get("health") or {}
    zero = lambda k: (h[k] == 0) if h.get(k) is not None else None        # noqa: E731
    return {"dropped_0": zero("dropped"), "write_fails_0": zero("fails"), "not_switched_off": (not h.get("off")) if h else None,
            "counts_add_up": (sum(o[k] for k in fs.CLASSES) == o["watched_minutes"]) if "watched_minutes" in o else None}


def verdict(checks: dict) -> str:
    """'outside: a, b' for the checks that failed and 'not measured: c' for the unknown ones (both when there are
    both: an unknown check is never hidden behind a failed one, nor read as a pass); 'inside' only when all hold."""
    bad, unknown = [k for k, v in checks.items() if v is False], [k for k, v in checks.items() if v is None]
    return "; ".join(x for x in (bad and "outside: " + ", ".join(bad), unknown and "not measured: " + ", ".join(unknown))
                     if x) or "inside"


def _plain(checks: dict) -> tuple[str, bool]:
    """A group of checks in the owner's words, and whether any failed. No check at all is 'not measured'."""
    bad, unknown = [PLAIN[k] for k, v in checks.items() if v is False], [PLAIN[k] for k, v in checks.items() if v is None]
    if not checks:
        return "not measured", False
    return "; ".join(x for x in (bad and "OUTSIDE: " + ", ".join(bad), unknown and "not measured: " + ", ".join(unknown))
                     if x) or "inside", bool(bad)


def breaches(o: dict) -> list[dict]:
    """Every check of the session that is confirmed outside (False), with what was observed and its limit. A check
    that was not measured is not in the list."""
    p, h, t, out = o.get("pace") or {}, o.get("health") or {}, TOL, []
    seen = {**(p.get("inside") or {}), **(o.get("integrity") or {})}
    said = {
        "cadence": lambda: (f"{100 * p['within']:.2f}%", f"at least {100 * t['within_min']:.1f}%"),
        "late": lambda: (str(p["late"]), str(t["late_max"])),
        "work_p90": lambda: (f"{p['work_p90_s']} s", f"{t['work_p90_max_s']} s"),
        "work_max": lambda: (f"{p['work_max_s']} s", f"{t['work_max_max_s']} s"),
        "dropped_0": lambda: (str(h.get("dropped")), "0"),
        "write_fails_0": lambda: (str(h.get("fails")), "0"),
        "not_switched_off": lambda: (str((h.get("off") or {}).get("why"))[:120], "not switched off"),
        "counts_add_up": lambda: (f"{sum(o[k] for k in fs.CLASSES)} classified of {o['watched_minutes']} watched", "equal"),
    }
    for k, v in seen.items():
        if v is False:
            try:
                observed, limit = said[k]()
            except Exception:  # noqa: BLE001 - the breach stands even if its figure cannot be put in words
                observed, limit = "figure unavailable", "see the session's line"
            out.append({"check": k, "metric": PLAIN[k], "observed": observed, "limit": limit})
    return out


def _off_at(db: Path) -> str | None:
    """When the recorder says it went off (its own database, read-only); None when it has not said so."""
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            r = c.execute("SELECT value FROM meta WHERE key='off_at'").fetchone()
        finally:
            c.close()
        return r[0] if r else None
    except sqlite3.Error:
        return None


def keep_evidence(o: dict, rows: list[str], folder: Path = EVIDENCE) -> str:
    """The session's line and the long book's raw cadence rows of the window, in a file of their own: the day files
    of the cadence record are removed after 60 days, this is not. Never written over: the first one stands."""
    path = Path(folder) / f"{o['session']}.json"
    if path.exists():
        return f"{path} (already kept)"
    Path(folder).mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"session": o["session"], "kept_at": datetime.now(timezone.utc).isoformat(),
                               "line": {k: v for k, v in o.items() if k != "shutoff"}, "cadence_rows": rows}, sort_keys=True))
    try:
        os.link(tmp, path)                              # whole or not at all, and never over an existing file
    except FileExistsError:
        return f"{path} (already kept)"
    finally:
        tmp.unlink(missing_ok=True)
    return str(path)


def _acknowledged(db: Path, wait_s: float, sleep) -> float | None:
    """Seconds until the recorder's own database says it is off (its writer's last act; a fresh start clears it,
    and it cannot start while the file exists), or None when it has not said so within ``wait_s``."""
    waited, step = 0.0, 0.5
    while True:
        if _off_at(db):
            return waited
        if waited >= wait_s:
            return None
        sleep(step)
        waited += step


def shutoff(o: dict, db: Path = fs.DB, rows: list[str] | None = None, evidence: Path = EVIDENCE,
            wait_s: float = CONFIRM_S, sleep=time.sleep, found: list[dict] | None = None) -> dict | None:
    """On a confirmed breach: keep the evidence, then create the recorder's OFF file, whole or not at all and never
    over an existing one (a temporary file linked into place), then wait for the running recorder to acknowledge.
    Returns what happened, for the session's line and the summary text; None when no check is outside.

    Results: "created" (made and acknowledged), "already present" (the file was there and the recorder says it is
    off), "already off" (see below), "unconfirmed" (the file exists but the recorder did not acknowledge within
    ``wait_s``: NOT a success), "failed" (the file could not be made in ATTEMPTS tries: the recorder is still on).
    Never raises. It never removes the file, never stops or signals a process, and nothing else is touched.

    A recorder that already says it is off (its own trip, or an earlier switch) is left as it is and no file is
    made: there is nothing to stop, and a person who has removed the file to bring it back at the next deploy is
    not overruled by a later night's check. ``found``: the breaches of an earlier session whose switch is retried."""
    found = found or breaches(o)
    if not found:
        return None
    off, at = Path(db).parent / fs.OFF_NAME, datetime.now(timezone.utc)
    out = {"breaches": found, "at": at.isoformat(), "file": str(off)}
    was_off = (o.get("health") or {}).get("off")
    if was_off:
        return {**out, "result": "already off", "file_present": off.exists(),
                "note": f"off since {was_off.get('at') or 'an unrecorded time'}: {str(was_off.get('why'))[:120]}"}
    try:
        out["evidence"] = keep_evidence(o, rows or [], evidence)
    except Exception as e:  # noqa: BLE001 - said; the switch is still made
        out["evidence"] = f"not kept ({type(e).__name__}: {str(e)[:100]})"
    why = (f"{at.isoformat()} switched off by the nightly check of session {o['session']}: "
           + "; ".join(f"{b['metric']} {b['observed']} (limit {b['limit']})" for b in found) + "\n")
    tmp, made, error = off.with_name(f".{fs.OFF_NAME}.{os.getpid()}.tmp"), None, None
    for attempt in range(1, ATTEMPTS + 1):              # bounded: a full volume or a refusal is not tried for ever
        out["attempts"] = attempt
        try:
            if off.exists():
                made = "already present"
                break
            off.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(why, encoding="utf-8")
            try:
                os.link(tmp, off)
                made = "created"
            except FileExistsError:
                made = "already present"
            break
        except Exception as e:  # noqa: BLE001 - e.g. a full volume or no permission
            error = f"{type(e).__name__}: {str(e)[:120]}"
            if attempt < ATTEMPTS:
                sleep(ATTEMPT_PAUSE_S)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if made is None:
        return {**out, "result": "failed", "error": error}
    took = _acknowledged(db, wait_s, sleep)
    if took is None:                                    # the file is there; the running recorder has not answered
        return {**out, "result": "unconfirmed", "file": str(off), "made": made}
    return {**out, "result": made, "confirmed_after_s": took}


def recheck(prev: dict, db: Path = fs.DB, wait_s: float = CONFIRM_S, sleep=time.sleep) -> dict:
    """A later run's look at a switch that was made but not acknowledged. It never makes the file: if a person has
    removed it since, that is their choice and nothing is done; if it is still there, the recorder is asked once
    more whether it is off."""
    off, at = Path(db).parent / fs.OFF_NAME, datetime.now(timezone.utc)
    out = {"breaches": prev.get("breaches") or [], "at": at.isoformat(), "file": str(off)}
    try:
        if not off.exists():
            return {**out, "result": "removed by a person"}
        took = _acknowledged(db, wait_s, sleep)
    except Exception as e:  # noqa: BLE001 - said, never raised
        return {**out, "result": "unconfirmed", "error": f"{type(e).__name__}: {str(e)[:120]}"}
    return {**out, "result": "unconfirmed"} if took is None else {**out, "result": "created", "confirmed_after_s": took}


def state(sh: dict | None) -> str | None:
    """The result of a stored switch as it counts now. A line written before 2026-11-27 (night) could say "created"
    without the recorder's acknowledgment (no number of seconds): that is "unconfirmed", never a success."""
    r = (sh or {}).get("result")
    if r == "created" and not isinstance((sh or {}).get("confirmed_after_s"), (int, float)):
        return "unconfirmed"
    return r


def _nights(sh: dict | None) -> int:
    try:
        return max(1, int((sh or {}).get("nights") or 1))
    except (TypeError, ValueError):
        return 1


def still_open(prev: dict | None) -> dict | None:
    """The switch of an earlier session that is still open (failed or unconfirmed) and may be taken up again, with
    its result as it counts now."""
    sh = (prev or {}).get("shutoff") or {}
    return {**sh, "result": state(sh)} if state(sh) in OPEN and _nights(sh) < MAX_NIGHTS else None


def _shutoff_words(sh: dict | None) -> str:
    if not sh:
        return f"{POLICY}."
    try:
        hm = f"{pd.Timestamp(sh['at']).tz_convert(fs.ET):%H:%M} ET" if sh.get("at") else "an unrecorded time"
    except (TypeError, ValueError):                     # a stored line is data: a stamp that cannot be read is said
        hm = "an unrecorded time"
    r, last = state(sh), _nights(sh) >= MAX_NIGHTS
    again = (" No further automatic retry: act by hand." if last else
             f" The next scheduled run tries again (run {_nights(sh)} of {MAX_NIGHTS}).")
    if r == "created":
        return (f"RECORDER SWITCHED OFF by this check at {hm}: OFF file created; the recorder confirmed off after "
                f"{sh['confirmed_after_s']:.1f} s. It stays off until a person removes the file.")
    if r == "already present":
        return ("OFF file was already there and is left as it is; the recorder confirms it is off. It stays off until a "
                "person removes the file.")
    if r == "already off":
        return (f"The recorder was already off ({sh.get('note')}): nothing was switched; OFF file "
                + ("present." if sh.get("file_present") else "not present: it stays off until its process restarts."))
    if r == "unconfirmed":
        return (f"SHUTOFF UNCONFIRMED at {hm}: the OFF file is in place, but the running recorder did not acknowledge "
                f"within {CONFIRM_S:.0f} s. It cannot start again while the file exists; whether it still records in the "
                "running process is not known. No trading process was stopped." + again)
    if r == "failed":
        return (f"SHUTOFF FAILED at {hm} after {sh.get('attempts', ATTEMPTS)} tries ({sh.get('error')}): THE RECORDER IS "
                "STILL ON. Create reports/feed_shadow/OFF on the runner by hand." + again)
    if r == "removed by a person":
        return "The OFF file of that switch has been removed by a person since: that choice stands and nothing was done."
    return f"The recorder was not switched by this run ({sh.get('note') or r})."


def report(o: dict) -> str:
    """The frozen check of one session as ONE short line for the midnight summary text (``sgt.ops.summary`` reads it
    from this session's line): the timing verdict and the integrity verdict; for a figure that is outside its name,
    what was observed and the limit, and what happened to the recorder (``shutoff``), before the other figures so
    that a cut text never loses them; what was not measured."""
    p = o.get("pace") or {}
    timing, bad_t = _plain(p.get("inside") or {})
    whole, bad_i = _plain(o.get("integrity") or {})
    bad = bad_t or bad_i
    if "within" in p:
        t = p["tolerance"]
        figures = (f"{p['passes']} passes, {100 * p['within']:.2f}% on time (limit at least {100 * t['within_min']:.1f}%), "
                   f"late {p['late']} (limit {t['late_max']}), longest pass per 10 min: 90th percentile {p['work_p90_s']} s "
                   f"(limit {t['work_p90_max_s']}), maximum {p['work_max_s']} s (limit {t['work_max_max_s']}), "
                   f"{o.get('longs_entered', 0)} longs entered")
    else:
        figures = p.get("note") or "no timing figure"
    sh = o.get("shutoff") or {}
    said = "; ".join(f"{b['metric']} {b['observed']} (limit {b['limit']})" for b in (breaches(o) if bad else sh.get("breaches") or []))
    outside = (f"Outside: {said}. " + _shutoff_words(o.get("shutoff")) + " ") if bad else (
        f"Switch still open from session {sh['pending_from']} ({said}): " + _shutoff_words(sh) + " ") if sh.get("pending_from") else ""
    warn = bad or sh.get("result") in OPEN
    return (("⚠️ " if warn else "") + f"feed recorder {o['session']}"
            + ("" if p.get("full_session") else " (not a full session)") + f": timing {timing}; integrity {whole}. "
            + outside + figures + (f"; {p['incomplete']}" if p.get("incomplete") else "")
            + (". The record resolves 0.1 s per 10 minutes: inside is not proof of no effect."
               if timing == "inside" and not bad else "."))
