"""ARCHIVED 2026-11-11 (owner chose "real long scoring"): long_book.settle as it was, official = the long shadow's
SIP RE-DETECTION of the same setup kind. Superseded because a re-detected setup can be a different trade (KULR 11-10:
live IEX trigger 2.155 stopped -1.2%, shadow SIP trigger 2.115 +1.63%). Kept for reference only; not imported."""

def settle(db: OpsStore, now: datetime | None = None) -> list[str]:
    """After the shadow run: book each closed live long at its official shadow net (same alert, same setup). Void,
    never left hanging (review F-1: a silent drop would bias the record, mostly losing L7 trades): a matching setup
    that did not fill on SIP; an event the shadow processed without that setup (e.g. IEX gaps looked like a halt
    live, SIP had none); a long whose alert never became an official event within STALE_DAYS."""
    now = now or utcnow()
    today = now.astimezone(ET).date().isoformat()
    with db.tx() as c:                    # final pass F-1: a long still open from an earlier session (listener down
        c.execute("UPDATE long_trades SET status='est_closed', reason='closed by settle (listener missed the exit)' "
                  "WHERE status='open' AND session<?", (today,))      # across the close) gets its official result
        rows = [dict(r) for r in c.execute(
            "SELECT t.id, t.symbol, t.kind, s.net, s.entry FROM long_trades t JOIN long_shadow s ON s.fade_id=t.fade_id "
            "AND s.kind=t.kind WHERE t.status='est_closed'")]
        orphans = [dict(r) for r in c.execute(
            "SELECT t.id, t.symbol, t.kind, t.session, EXISTS(SELECT 1 FROM long_shadow s WHERE s.fade_id=t.fade_id) "
            "AS seen FROM long_trades t WHERE t.status='est_closed' AND NOT EXISTS (SELECT 1 FROM long_shadow s WHERE "
            "s.fade_id=t.fade_id AND s.kind=t.kind)")]
    out = []
    stale = (now.astimezone(ET).date() - timedelta(days=STALE_DAYS)).isoformat()
    for r in orphans:
        why = ("official bars show a different setup (IEX vs SIP)" if r["seen"] else
               "no official event" if r["session"] <= stale else None)
        if why and _set(db, r["id"], "est_closed", status="void", reason=why):
            out.append(f"{r['symbol']} {r['kind']} void")
    for r in rows:
        if r["net"] is None:
            _set(db, r["id"], "est_closed", status="void", reason="setup did not fill on SIP bars")
            out.append(f"{r['symbol']} {r['kind']} void")
        elif _set(db, r["id"], "est_closed", status="settled", net=float(r["net"])):
            out.append(f"{r['symbol']} {r['kind']} {float(r['net']):+.2%}")
    return out
