"""Fast check of the alert channel (owner 2026-11-18): each message handled once, bounded, obeys Telegram's slow-down."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sgt.fast import chan_poll as cp


def msg(i):
    return SimpleNamespace(id=i, message=f"m{i}", date=datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc))


class Channel:
    def __init__(self, ids):
        self.ids, self.calls = list(ids), []

    async def get(self, **kw):
        self.calls.append(kw)
        if "min_id" not in kw:
            return [msg(i) for i in sorted(self.ids, reverse=True)[:kw["limit"]]]
        return [msg(i) for i in sorted((i for i in self.ids if i > kw["min_id"]), reverse=True)[:kw["limit"]]]


def run(coro):
    return asyncio.run(coro)


def test_the_first_check_only_sets_the_baseline():
    ch, st, got = Channel([5, 6, 7]), cp.State(), []
    assert run(cp.poll_once(ch.get, st, got.append)) == 0 and got == [] and st.last_id == 7
    assert ch.calls == [{"limit": 1}]


def test_new_messages_are_handled_once_oldest_first():
    ch, st, got = Channel([7]), cp.State(), []
    run(cp.poll_once(ch.get, st, got.append))
    ch.ids += [9, 8]
    assert run(cp.poll_once(ch.get, st, got.append)) == 2 and [m.id for m in got] == [8, 9] and st.last_id == 9
    assert ch.calls[-1] == {"min_id": 7, "limit": cp.MAX_BATCH}
    assert run(cp.poll_once(ch.get, st, got.append)) == 0 and len(got) == 2          # nothing new: nothing handled


def test_a_message_the_push_path_already_took_is_not_handled_again():
    ch, st, got = Channel([7]), cp.State(), []
    run(cp.poll_once(ch.get, st, got.append))
    assert st.seen.add(8)                                       # push saw message 8 first
    ch.ids += [8, 9]
    assert run(cp.poll_once(ch.get, st, got.append)) == 1 and [m.id for m in got] == [9] and st.last_id == 9
    assert not st.seen.add(9)                                   # and push, arriving later, finds 9 handled


def test_a_message_the_poll_finds_first_reaches_the_books_exactly_once():
    """The bug of 2026-11-18/19: poll and push shared one guarded handler, so a message the poll found first was
    marked seen by the poll and then refused by the handler, and the later push was refused too: the alert was lost.
    With the listener's real wiring (the guard for push, the work itself for the poll) every message is dispatched
    once, whichever path sees it first."""
    ch, st, done = Channel([7]), cp.State(), []
    dispatch = lambda m, how: done.append((m.id, how))                  # noqa: E731
    take = cp.pushed(st, dispatch)
    run(cp.poll_once(ch.get, st, lambda m: dispatch(m, "poll")))       # baseline
    ch.ids += [8]
    assert run(cp.poll_once(ch.get, st, lambda m: dispatch(m, "poll"))) == 1 and done == [(8, "poll")]   # poll first
    assert take(msg(8)) is False and done == [(8, "poll")]             # the late push of the same message: not twice
    assert take(msg(9)) is True and done[-1] == (9, "push")            # push first
    ch.ids += [9, 10]
    assert run(cp.poll_once(ch.get, st, lambda m: dispatch(m, "poll"))) == 1 and done == [(8, "poll"), (9, "push"), (10, "poll")]
    assert take(msg(10)) is False and take(msg(9)) is False and len(done) == 3
    # the old wiring, kept here as the proof of the defect: a guarded handler given to the poll drops the message
    st2, lost = cp.State(), []
    old = cp.pushed(st2, lambda m, how: lost.append(m.id))
    ch2 = Channel([1])
    run(cp.poll_once(ch2.get, st2, old))
    ch2.ids += [2]
    assert run(cp.poll_once(ch2.get, st2, old)) == 1 and lost == []    # "handled 1", dispatched nothing


def test_old_or_malformed_messages_are_ignored():
    st, got = cp.State(last_id=10), []

    async def get(**kw):
        return [msg(12), msg(10), msg(3), SimpleNamespace(id="x"), SimpleNamespace(), msg(11)]
    assert run(cp.poll_once(get, st, got.append)) == 2 and [m.id for m in got] == [11, 12]


def test_a_batch_is_bounded():
    st, got = cp.State(last_id=0), []

    async def get(**kw):
        return [msg(i) for i in range(1, 500)]
    assert run(cp.poll_once(get, st, got.append)) == cp.MAX_BATCH


def test_an_empty_channel_baseline_is_zero():
    st = cp.State()

    async def get(**kw):
        return []
    assert run(cp.poll_once(get, st, lambda m: None)) == 0 and st.last_id == 0


def test_seen_is_bounded_and_forgets_the_oldest():
    s = cp.Seen(cap=3)
    assert all(s.add(i) for i in (1, 2, 3)) and not s.add(2)
    assert s.add(4) and s.add(1)                                # 1 was dropped when 4 came in
    assert len(s._ids) == 3


@pytest.mark.parametrize("value,want", [(None, cp.EVERY), ("", cp.EVERY), ("abc", cp.EVERY), ("0", 0), ("30", 30),
                                         ("1", cp.MIN_S), ("9999", cp.MAX_S), ("-5", cp.MIN_S), (" 20 ", 20)])
def test_interval_from_the_flag(value, want):
    assert cp.interval(value) == want


def test_active_only_in_session_hours():
    def at(s):
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    assert cp.active(at("2026-11-18T14:25:00")) and cp.active(at("2026-11-18T20:59:59"))      # 09:25 and 15:59:59 ET
    assert not cp.active(at("2026-11-18T14:24:59")) and not cp.active(at("2026-11-18T21:00:00"))
    assert not cp.active(at("2026-11-21T16:00:00"))                                           # a Saturday


@pytest.mark.parametrize("asked,want", [(30, 31), (0, 2), (-4, 2), (100_000, cp.FLOOD_CAP_S + 1), (None, 61), ("x", 61)])
def test_flood_pause_obeys_and_is_capped(asked, want):
    assert cp.flood_pause(asked) == want


def test_the_listener_runs_the_check_and_the_shorter_intervals():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fade_live as fl

    src = Path(fl.__file__).read_text()
    assert fl.LONG_EVERY == 15 and fl.TAPE_EVERY == 30 and fl.TICK == 10
    assert "asyncio.create_task(channel_loop())" in src
    # 2026-11-19: one "seen" check per message. The push path goes through the guard; the poll gets the work itself.
    assert "take = chan_poll.pushed(poll_state, dispatch)" in src and "take(ev.message)" in src
    assert 'lambda m: dispatch(m, "poll")' in src and 'take(m, "poll")' not in src
