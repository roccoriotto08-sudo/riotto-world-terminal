from datetime import date, timedelta

from backend.app.db import DB
from backend.app.pipeline import Pipeline
from backend.app.schema import AdapterResult, Observation


def o(entity, metric, value, d, source="JODI", unit="KBD", cadence="monthly"):
    return Observation(entity=entity, metric=metric, value=value, unit=unit, observation_date=d,
                       source=source, release_cadence=cadence)


def month(n_back):
    d = date.today().replace(day=1)
    for _ in range(n_back):
        d = (d - timedelta(days=1)).replace(day=1)
    return d.isoformat()


def alerts(db):
    return db.events(kind="ALERT")


def test_output_drop_fires_once_with_trigger_points(tmp_path):
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    p.ingest("JODI", AdapterResult(observations=[o("SAU", "crude_prod", 9000, month(3))]))
    p.ingest("JODI", AdapterResult(observations=[o("SAU", "crude_prod", 8500, month(2))]))   # -5.6% m/m
    a = alerts(db)
    assert len(a) == 1
    t = a[0]["trigger"]
    assert t["current"]["value"] == 8500 and t["reference"]["value"] == 9000
    assert t["stat"]["pct_change"] < -3
    assert a[0]["readthrough"]["assets"][0]["asset"] == "BRENT"
    # re-ingesting the same data must not create a duplicate alert
    p.ingest("JODI", AdapterResult(observations=[o("SAU", "crude_prod", 8500, month(2))]))
    assert len(alerts(db)) == 1


def test_no_alert_on_backfilled_history(tmp_path):
    db = DB(tmp_path / "t.db")
    Pipeline(db).ingest("JODI", AdapterResult(observations=[
        o("IRQ", "crude_prod", 4500, "2020-03-01"), o("IRQ", "crude_prod", 3800, "2020-04-01")]))
    assert alerts(db) == []


def test_small_producer_ignored(tmp_path):
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    p.ingest("JODI", AdapterResult(observations=[o("PHL", "crude_prod", 10, month(3))]))
    p.ingest("JODI", AdapterResult(observations=[o("PHL", "crude_prod", 5, month(2))]))
    assert alerts(db) == []


def test_chokepoint_baseline_rule(tmp_path):
    db = DB(tmp_path / "t.db")
    end = date.today() - timedelta(days=8)
    rows = []
    for i in range(97):
        d = end - timedelta(days=96 - i)
        v = 40.0 if i < 90 else 10.0          # last 7 days collapse to 25% of baseline
        rows.append(o("HORMUZ", "transits_total", v, d.isoformat(), "PORTWATCH", "vessels/day", "daily"))
    Pipeline(db).ingest("PORTWATCH", AdapterResult(observations=rows))
    a = alerts(db)
    assert len(a) == 1 and a[0]["rule_id"] == "chokepoint_flow_drop"
    assert a[0]["trigger"]["stat"]["pct_vs_baseline"] == -75.0
    assert a[0]["lat"] is not None


def test_policy_rate_change(tmp_path):
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    d0 = (date.today() - timedelta(days=5)).isoformat()
    d1 = (date.today() - timedelta(days=1)).isoformat()
    p.ingest("BIS", AdapterResult(observations=[o("NOR", "policy_rate", 4.25, d0, "BIS", "%", "daily")]))
    p.ingest("BIS", AdapterResult(observations=[o("NOR", "policy_rate", 4.50, d1, "BIS", "%", "daily")]))
    a = alerts(db)
    assert len(a) == 1 and "+0.25pp" in a[0]["title"]
    assert a[0]["readthrough"]["assets"][0]["direction"] == "up"


def test_curve_inversion_deepens(tmp_path):
    db = DB(tmp_path / "t.db")
    p = Pipeline(db)
    d0 = (date.today() - timedelta(days=8)).isoformat()
    d1 = (date.today() - timedelta(days=1)).isoformat()
    p.ingest("FRED", AdapterResult(observations=[o("US2S10S", "spread", -0.10, d0, "FRED", "%", "daily")]))
    p.ingest("FRED", AdapterResult(observations=[o("US2S10S", "spread", -0.25, d1, "FRED", "%", "daily")]))
    a = alerts(db)
    assert len(a) == 1 and a[0]["rule_id"] == "us_curve_inversion_deepens"
