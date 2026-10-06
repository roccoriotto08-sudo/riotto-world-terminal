from datetime import date, timedelta

from backend.app.db import DB
from backend.app.pipeline import Pipeline
from backend.app.sanity import SanityChecker
from backend.app.schema import AdapterResult, Observation
from backend.app.settings import SETTINGS


def obs(entity, metric, value, d, source="TEST", unit="USD/bbl", cadence="daily"):
    return Observation(entity=entity, metric=metric, value=value, unit=unit, observation_date=d,
                       source=source, release_cadence=cadence)


def test_brent_zero_rejected():
    s = SanityChecker(SETTINGS.metrics["bounds"])
    ok, reason = s.check("BRENT", "price", 0.0)
    assert not ok and "floor" in reason


def test_brent_300pct_move_rejected():
    s = SanityChecker(SETTINGS.metrics["bounds"])
    ok, reason = s.check("BRENT", "price", 320.0, prev_value=80.0)
    assert not ok and "%" in reason


def test_wti_negative_allowed():
    s = SanityChecker(SETTINGS.metrics["bounds"])
    assert s.check("WTI", "price", -37.63)[0]


def test_wildcard_bounds_apply():
    s = SanityChecker(SETTINGS.metrics["bounds"])
    assert not s.check("SAU", "crude_prod", 30000)[0]   # kb/d above any country
    assert s.check("SAU", "crude_prod", 9000)[0]


def test_pipeline_quarantines_and_recovers(tmp_path):
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    res = AdapterResult(observations=[
        obs("BRENT", "price", 80.0, "2026-01-02"),
        obs("BRENT", "price", 0.0, "2026-01-05"),      # bad print
        obs("BRENT", "price", 81.0, "2026-01-06"),      # normal again -> accepted
    ])
    summary = p.ingest("TEST", res)
    assert summary["quarantined"] == 1
    assert db.latest("BRENT", "price", "TEST")["value"] == 81.0
    assert db.quarantine_log()[0]["value"] == 0.0


def test_level_shift_accepted_after_confirmation(tmp_path):
    # Argentina-style genuine regime change: 133 -> 100 -> 100 (move > bound)
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    p.ingest("TEST", AdapterResult(observations=[
        obs("ARG", "policy_rate", 133.0, "2023-11-30", unit="%"),
        obs("ARG", "policy_rate", 90.0, "2023-12-18", unit="%"),   # -43 > 40 bound: quarantined
        obs("ARG", "policy_rate", 90.0, "2023-12-31", unit="%"),   # confirms the new level
        obs("ARG", "policy_rate", 80.0, "2024-03-12", unit="%"),
    ]))
    latest = db.latest("ARG", "policy_rate", "TEST")
    assert latest["value"] == 80.0
    assert len(db.quarantine_log()) == 1


def test_units_normalized_before_storage(tmp_path):
    db = DB(tmp_path / "t.db")
    Pipeline(db).ingest("TEST", AdapterResult(observations=[
        obs("USA", "crude_stocks_comm", 415000, "2026-09-26", unit="MBBL", cadence="weekly")]))
    r = db.latest("USA", "crude_stocks_comm", "TEST")
    assert r["unit"] == "mb" and abs(r["value"] - 415.0) < 1e-9


def test_unknown_unit_quarantined(tmp_path):
    db = DB(tmp_path / "t.db")
    Pipeline(db).ingest("TEST", AdapterResult(observations=[obs("X", "y", 1.0, "2026-01-01", unit="furlongs")]))
    assert db.latest("X", "y") is None
    assert "unit" in db.quarantine_log()[0]["reason"]


def test_revision_is_recorded(tmp_path):
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    p.ingest("TEST", AdapterResult(observations=[obs("BRENT", "price", 80.0, "2026-01-02")]))
    s = p.ingest("TEST", AdapterResult(observations=[obs("BRENT", "price", 80.5, "2026-01-02")]))
    assert s["revised"] == 1 and s["new"] == 0
    assert "REVISED" in db.latest("BRENT", "price", "TEST")["flags"]


def test_crosscheck_flags_eia_vs_jodi(tmp_path):
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    m = (date.today().replace(day=1) - timedelta(days=40)).replace(day=1).isoformat()
    p.ingest("JODI", AdapterResult(observations=[obs("SAU", "crude_prod", 9000, m, "JODI", "KBD", "monthly")]))
    s = p.ingest("EIA", AdapterResult(observations=[obs("SAU", "crude_prod", 9600, m, "EIA", "TBPD", "monthly")]))
    assert s["discrepancies"] == 1
    d = db.discrepancies()[0]
    assert d["entity"] == "SAU" and abs(d["pct"]) > 5
