"""Control-plane API: fail-closed auth, state changes, audit, manual-fill hand-off (§14)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from sgt.accounts.registry import upsert_account
from sgt.api.control_app import app
from sgt.brokers import AlertOnlyBroker
from sgt.core.types import OrderIntent, OrderType, Side, TimeInForce
from sgt.db.session import get_session

UTC = timezone.utc
TOKEN = "s3cr3t-control-token"
ENV = "SGT_SECRET_CONTROL_PLANE_TOKEN"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv(ENV, TOKEN)
    return {"Authorization": f"Bearer {TOKEN}"}


class _Notifier:
    def send(self, title: str, body: str) -> None:  # pragma: no cover - trivial
        pass


# --------------------------------------------------------------------------- auth / fail-closed


def test_no_token_configured_returns_503(client, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    r = client.get("/control/status", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503


def test_wrong_token_returns_401(client, auth):
    r = client.get("/control/status", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_missing_header_returns_401(client, auth):
    r = client.get("/control/status")
    assert r.status_code == 401


def test_health_still_open_without_auth(client, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# --------------------------------------------------------------------------- status / arm / disarm


def test_status_ok_with_token(client, auth):
    r = client.get("/control/status", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body["namespaces"]) == {"live", "paper", "shadow"}
    assert body["accounts"] == []


def test_arm_and_disarm_paper(client, auth):
    r = client.post("/control/arm", headers=auth,
                    json={"namespace": "paper", "actor": "alice", "reason": "start soak"})
    assert r.status_code == 200 and r.json()["switch"]["armed"] is True
    st = client.get("/control/status", headers=auth).json()
    assert st["namespaces"]["paper"]["switch"]["armed"] is True
    r = client.post("/control/disarm", headers=auth,
                    json={"namespace": "paper", "actor": "alice", "reason": "done"})
    assert r.status_code == 200 and r.json()["switch"]["armed"] is False


def test_arm_requires_nonempty_actor_and_reason(client, auth):
    r = client.post("/control/arm", headers=auth,
                    json={"namespace": "paper", "actor": "", "reason": "x"})
    assert r.status_code == 422
    r = client.post("/control/arm", headers=auth,
                    json={"namespace": "paper", "actor": "a", "reason": ""})
    assert r.status_code == 422


def test_arm_live_requires_confirm(client, auth):
    r = client.post("/control/arm", headers=auth,
                    json={"namespace": "live", "actor": "alice", "reason": "go"})
    assert r.status_code == 400
    r = client.post("/control/arm", headers=auth,
                    json={"namespace": "live", "actor": "alice", "reason": "go",
                          "confirm": "ARM LIVE"})
    assert r.status_code == 200 and r.json()["switch"]["armed"] is True


def test_disarm_live_always_allowed(client, auth):
    r = client.post("/control/disarm", headers=auth,
                    json={"namespace": "live", "actor": "alice"})
    assert r.status_code == 200 and r.json()["switch"]["armed"] is False


# --------------------------------------------------------------------------- strategies


def test_stage_set_and_validation(client, auth):
    r = client.post("/control/strategies/trader_v0/stage", headers=auth,
                    json={"namespace": "paper", "stage": "micro", "actor": "alice"})
    assert r.status_code == 200 and r.json()["stage"] == "micro"
    st = client.get("/control/status", headers=auth).json()
    strats = st["namespaces"]["paper"]["strategies"]
    assert any(s["strategy_id"] == "trader_v0" and s["stage"] == "micro" for s in strats)
    bad = client.post("/control/strategies/trader_v0/stage", headers=auth,
                      json={"namespace": "paper", "stage": "rocket", "actor": "alice"})
    assert bad.status_code == 422


def test_disable_strategy(client, auth):
    client.post("/control/strategies/trader_v0/stage", headers=auth,
                json={"namespace": "paper", "stage": "micro", "actor": "alice"})
    r = client.post("/control/strategies/trader_v0/disable", headers=auth,
                    json={"namespace": "paper", "actor": "alice", "reason": "decay"})
    assert r.status_code == 200 and r.json()["enabled"] is False


# --------------------------------------------------------------------------- accounts


def test_account_enable_disable(client, auth):
    with get_session() as s:
        upsert_account(s, account_id="acct-1", broker="alpaca")
    r = client.post("/control/accounts/acct-1/disable", headers=auth, json={"actor": "alice"})
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = client.post("/control/accounts/acct-1/enable", headers=auth, json={"actor": "alice"})
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_account_unknown_returns_404(client, auth):
    r = client.post("/control/accounts/nope/disable", headers=auth, json={"actor": "alice"})
    assert r.status_code == 404


# --------------------------------------------------------------------------- audit


def test_audit_records_actor(client, auth):
    client.post("/control/arm", headers=auth,
                json={"namespace": "paper", "actor": "carol", "reason": "soak"})
    r = client.get("/control/audit", headers=auth, params={"namespace": "paper", "limit": 50})
    assert r.status_code == 200
    kinds = {row["kind"] for row in r.json()["audit"]}
    assert "control_arm" in kinds
    arm_row = next(row for row in r.json()["audit"] if row["kind"] == "control_arm")
    assert arm_row["detail"]["actor"] == "carol"


def test_audit_limit_bounded(client, auth):
    r = client.get("/control/audit", headers=auth, params={"namespace": "paper", "limit": 1000})
    assert r.status_code == 422


# --------------------------------------------------------------------------- invalid bodies


def test_extra_field_rejected(client, auth):
    r = client.post("/control/arm", headers=auth,
                    json={"namespace": "paper", "actor": "a", "reason": "r", "sneaky": 1})
    assert r.status_code == 422


def test_manual_fill_invalid_body(client, auth):
    with get_session() as s:
        upsert_account(s, account_id="acct-mf", broker="alert_only")
    r = client.post("/control/manual-fills", headers=auth, json={
        "account_id": "acct-mf", "client_order_id": "c1", "symbol": "AAA",
        "side": "buy", "qty": -1, "price": 5.0,
        "executed_at": "2026-10-12T14:40:00+00:00", "recorded_by": "op"})
    assert r.status_code == 422


def test_manual_fill_unknown_account_404(client, auth):
    r = client.post("/control/manual-fills", headers=auth, json={
        "account_id": "ghost", "client_order_id": "c1", "symbol": "AAA",
        "side": "buy", "qty": 10, "price": 5.0,
        "executed_at": "2026-10-12T14:40:00+00:00", "recorded_by": "op"})
    assert r.status_code == 404


# --------------------------------------------------------------------------- manual-fill end to end


async def test_manual_fill_end_to_end(client, auth):
    with get_session() as s:
        upsert_account(s, account_id="acct-e2e", broker="alert_only")
    broker = AlertOnlyBroker("acct-e2e", _Notifier())
    intent = OrderIntent(
        client_order_id="cid-e2e", account_id="acct-e2e", symbol="AAA", side=Side.BUY,
        qty=10, order_type=OrderType.LIMIT, limit_price=5.5, tif=TimeInForce.DAY,
        extended_hours=False, strategy_id="trader_v0", reason="entry",
        created_at=datetime(2026, 10, 12, 14, 30, tzinfo=UTC), stop_price=5.0)
    await broker.submit(intent)

    r = client.post("/control/manual-fills", headers=auth, json={
        "account_id": "acct-e2e", "client_order_id": "cid-e2e", "symbol": "AAA",
        "side": "buy", "qty": 10, "price": 5.25,
        "executed_at": "2026-10-12T14:40:00+00:00", "recorded_by": "op"})
    assert r.status_code == 200

    result = broker.poll_inbox()
    assert len(result.filled) == 1 and not result.rejected
    fills = [f async for f in broker.fills()]
    assert len(fills) == 1 and fills[0].price == 5.25 and fills[0].qty == 10

    # Inbox is drained: a second poll yields nothing.
    assert broker.poll_inbox().filled == []


async def test_manual_fill_rejected_recorded_not_dropped(client, auth):
    with get_session() as s:
        upsert_account(s, account_id="acct-rej", broker="alert_only")
    broker = AlertOnlyBroker("acct-rej", _Notifier())
    # No order submitted -> record_manual_fill will reject.
    r = client.post("/control/manual-fills", headers=auth, json={
        "account_id": "acct-rej", "client_order_id": "missing", "symbol": "AAA",
        "side": "buy", "qty": 10, "price": 5.25,
        "executed_at": "2026-10-12T14:40:00+00:00", "recorded_by": "op"})
    assert r.status_code == 200
    result = broker.poll_inbox()
    assert result.filled == [] and len(result.rejected) == 1
    assert "no submitted order" in result.rejected[0][1]


def test_non_ascii_token_is_401_not_500(client, auth):
    """Regression: str compare_digest raised TypeError on non-ASCII -> 500."""
    r = client.get("/control/status", headers={"Authorization": "Bearer töken-é".encode("latin-1")})
    assert r.status_code == 401


def test_public_signals_app_exposes_no_control_routes():
    """S-1 regression: the privileged router must not be mounted on the public app."""
    # Probe over HTTP: lazily-included routers do not expose paths in app.routes.
    from sgt.api.app import app as public_app

    public = TestClient(public_app)
    for method, path in _control_routes():
        url = path.replace("{strategy_id}", "s1").replace("{account_id}", "a1")
        assert public.request(method, url).status_code == 404, (method, url)


def test_manual_fill_rejected_for_non_alert_only_account(client, auth):
    """S-4 regression: inbox rows for API brokers would never be drained."""
    with get_session() as s:
        upsert_account(s, account_id="acct-api", broker="alpaca")
    r = client.post("/control/manual-fills", headers=auth, json={
        "account_id": "acct-api", "client_order_id": "c1", "symbol": "AAA",
        "side": "buy", "qty": 10, "price": 5.0,
        "executed_at": "2026-10-12T14:40:00+00:00", "recorded_by": "op"})
    assert r.status_code == 409


def test_account_toggle_audit_is_atomic_with_state_change(client, auth, monkeypatch):
    """S-3 regression: a failure while writing the audit row must roll back the toggle."""
    from sgt.accounts.models import Account
    from sgt.risk.state import RiskStore

    with get_session() as s:
        upsert_account(s, account_id="acct-atomic", broker="alpaca")

    def boom(self, session, kind, detail, ts=None):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(RiskStore, "append_audit_in", boom)
    with pytest.raises(RuntimeError):
        client.post("/control/accounts/acct-atomic/disable", headers=auth,
                    json={"actor": "op", "reason": "test", "namespace": "live"})
    with get_session() as s:
        assert s.get(Account, "acct-atomic").enabled is True


def _control_routes():
    # Enumerate from the router: FastAPI >=0.14x wraps included routers lazily in app.routes.
    from fastapi.routing import APIRoute

    from sgt.api.control import router as control_router

    return [(m, r.path) for r in control_router.routes if isinstance(r, APIRoute)
            for m in sorted(r.methods)]


@pytest.mark.parametrize("method,path", _control_routes())
def test_every_control_route_requires_token(client, auth, method, path):
    """T3: structural guard — no /control route may be reachable without a valid token."""
    url = path.replace("{strategy_id}", "s1").replace("{account_id}", "a1")
    assert client.request(method, url).status_code == 401
    assert client.request(method, url, headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize("method,path", _control_routes())
def test_every_control_route_fails_closed_without_configured_token(client, monkeypatch, method, path):
    monkeypatch.delenv(ENV, raising=False)
    url = path.replace("{strategy_id}", "s1").replace("{account_id}", "a1")
    assert client.request(method, url, headers={"Authorization": "Bearer x"}).status_code == 503


def test_control_route_inventory_is_non_trivial():
    assert len(_control_routes()) >= 9
