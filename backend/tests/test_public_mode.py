"""Public deployment: licence-restricted series must never be served, manual polling is off."""
import pytest
from fastapi.testclient import TestClient

import backend.app.main as m
from backend.app.adapters.acled import AcledAdapter
from backend.app.adapters.market import MarketAdapter
from backend.app.settings import SETTINGS


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setattr(SETTINGS, "public_mode", True)
    return TestClient(m.app)       # no lifespan: the scheduler is not started


def test_restricted_entities_are_derived_from_config():
    assert {"SPX", "HY_OAS", "VIX"} <= SETTINGS.restricted_entities
    assert "UST10Y" not in SETTINGS.restricted_entities


def test_hidden_everywhere(public):
    ids = [k["id"] for k in public.get("/api/meta").json()["kpis"]]
    assert "SPX" not in ids and "HYOAS" not in ids and "VIX" not in ids and "UST10Y" in ids
    assert "GOLD" not in ids          # only a vendor market feed can fill it, and that is off in public mode
    assert all(k["id"] not in ("SPX", "HYOAS", "VIX") for k in public.get("/api/kpis").json()["kpis"])
    assert all(s["entity"] not in ("SPX", "HY_OAS", "VIX") for s in public.get("/api/securities").json())
    assert public.get("/api/history", params={"entity": "SPX", "metric": "level"}).status_code == 404
    assert public.get("/api/des", params={"entity": "HY_OAS", "metric": "spread"}).status_code == 404
    assert public.get("/api/command", params={"q": "SPX INDEX GP"}).status_code == 404
    assert public.get("/api/kpi/SPX").status_code == 404
    assert public.get("/api/search", params={"q": "SPX"}).status_code == 404


def test_manual_poll_blocked(public):
    assert public.post("/api/poll/GDELT").status_code == 403


def test_licensed_feeds_off_by_default(public, monkeypatch):
    monkeypatch.setattr(SETTINGS, "market_provider", "twelvedata")
    monkeypatch.setattr(SETTINGS, "twelvedata_key", "x")
    db = m.db
    assert MarketAdapter(db).enabled()[0] is False
    monkeypatch.setattr(SETTINGS, "acled_email", "a"); monkeypatch.setattr(SETTINGS, "acled_password", "b")
    assert AcledAdapter(db).enabled()[0] is False


def test_private_mode_unchanged(monkeypatch):
    monkeypatch.setattr(SETTINGS, "public_mode", False)
    c = TestClient(m.app)
    assert "SPX" in [k["id"] for k in c.get("/api/meta").json()["kpis"]]


def test_healthz_and_credits(public):
    assert public.get("/healthz").json() == {"ok": True}
    c = public.get("/api/credits").json()
    assert c["public_mode"] and "SPX" in c["hidden_series"] and len(c["sources"]) >= 10
