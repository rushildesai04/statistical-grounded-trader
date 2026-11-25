"""Putting a built page in place (2026-11-24). One job, three guarantees:

1. The page is replaced in one step (a rename), so the server never sends half a page.
2. Each build writes a temporary file of its OWN. 2026-11-24 16:14 ET: the container's start-up chart build and the
   deploy script's ran 3 s apart and shared one ``fade_tape.tmp``; the first renamed it away, the second exited 1
   (FileNotFoundError) and the nightly listed "jobs FAILED: charts". The page itself was whole.
3. An OLDER build never replaces a NEWER page. Two builds can overlap (a deploy, a build by hand, a slow run that the
   next scheduled one overtakes), and the one that started first can finish last: its data is older. Every build
   says when it read its data (``built_from``, taken before the first read); the page's time is kept beside it
   (``<page>.built``) and compared under a file lock, so compare-and-replace is one step across processes.

A build that is dropped is not a failure: the newer page is already there. Nothing else is touched.
"""
from __future__ import annotations

import fcntl
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_TMP_S = 3600                    # a build takes seconds: a temporary file this old was left by a killed run


def _when(text: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def publish(out: Path, html: str, built_from: datetime) -> bool:
    """Put ``html`` at ``out``. ``built_from``: when this build read its data (aware). False = a page built from
    newer data is already in place and this one was dropped. A stamp that cannot be read does not block a publish."""
    if built_from.tzinfo is None:
        raise ValueError("built_from must carry a time zone")
    out.parent.mkdir(parents=True, exist_ok=True)
    for old in out.parent.glob(out.stem + ".*.tmp"):     # a run killed mid-write (a deploy) left its file: bounded
        try:
            if time.time() - old.stat().st_mtime > STALE_TMP_S:
                old.unlink()
        except OSError:                                  # another run took it first: nothing to clean
            pass
    fd, name = tempfile.mkstemp(dir=out.parent, prefix=out.stem + ".", suffix=".tmp")
    tmp, stamp = Path(name), out.with_name(out.name + ".built")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.chmod(tmp, 0o644)                             # mkstemp makes it 0600; the chart server only needs to read
        with open(out.with_name(out.name + ".lock"), "a") as lock:   # noqa: PTH123 - a lock file, kept open on purpose
            fcntl.flock(lock, fcntl.LOCK_EX)             # released when the file closes, also if this process dies
            try:
                have = _when(stamp.read_text(encoding="utf-8")) if stamp.exists() else None
            except OSError:
                have = None
            if have is not None and have > built_from:
                return False                             # the page in place was built from newer data
            tmp.replace(out)
            fd2, name2 = tempfile.mkstemp(dir=out.parent, prefix=out.stem + ".", suffix=".tmp")
            with os.fdopen(fd2, "w", encoding="utf-8") as f:
                f.write(built_from.isoformat())
            os.chmod(name2, 0o644)
            Path(name2).replace(stamp)
        return True
    finally:
        tmp.unlink(missing_ok=True)                      # a failed or dropped build leaves nothing behind
