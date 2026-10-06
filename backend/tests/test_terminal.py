from datetime import date, timedelta
import json

from backend.app.analytics import Analytics
from backend.app.db import DB
from backend.app.pipeline import Pipeline
from backend.app.schema import AdapterResult, Observation
from backend.app.securities import parse
from backend.app.status import StatusResolver


def test_parse_full_grammar():
    r = parse("CL1 COMDTY GP")
    assert r["security"]["entity"] == "WTI" and r["yk"] == "COMDTY" and r["function"] == "GP"
    assert parse("usgg10yr govt")["security"]["entity"] == "UST10Y"
    assert parse("XOM US EQUITY DES")["security"]["entity"] == "EQ:XOM"
    assert parse("WTI")["function"] is None          # bare ticker -> numbered menu


def test_parse_wrong_yellow_key_is_an_error_not_a_guess():
    assert "not a GOVT" in parse("CL1 GOVT")["error"]


def test_fx_ticker_matches_stored_direction():
    # stored as EUR per USD, so it must not be offered as EURUSD
    assert parse("EURUSD") is None
    assert parse("USDEUR")["security"]["metric"] == "fx_per_usd"


def test_unknown_returns_none():
    assert parse("NOTATICKER") is None


def _an(tmp_path):
    db = DB(tmp_path / "t.db")
    return db, Pipeline(db), Analytics(db, StatusResolver({}, db.states))


def eco_obs(series, d, v):
    return Observation(entity="US_ECO", metric=series, value=v, unit="index", observation_date=d,
                       source="FRED", release_cadence="monthly", period_label=d[:7])


def test_eco_transforms(tmp_path):
    db, p, an = _an(tmp_path)
    rows = [eco_obs("CPIAUCSL", f"2025-{m:02d}-01", 300 + m) for m in range(1, 13)]
    rows += [eco_obs("CPIAUCSL", "2026-01-01", 313.0), eco_obs("CPIAUCSL", "2026-02-01", 315.0)]
    p.ingest("FRED", AdapterResult(observations=rows))
    yoy = an._eco_values("CPIAUCSL", "yoy")
    assert abs(yoy["latest"] - (315 / 302 - 1) * 100) < 1e-9      # Feb-26 vs Feb-25
    assert abs(yoy["prior"] - (313 / 301 - 1) * 100) < 1e-9
    mom = an._eco_values("CPIAUCSL", "mom")
    assert abs(mom["latest"] - (315 / 313 - 1) * 100) < 1e-9
    assert an._eco_values("CPIAUCSL", "chg")["latest"] == 2.0


def test_eco_awaiting_fred_never_shows_stale_value_as_latest(tmp_path):
    db, p, an = _an(tmp_path)
    p.ingest("FRED", AdapterResult(observations=[eco_obs("PAYEMS", "2026-07-01", 100), eco_obs("PAYEMS", "2026-08-01", 110)]))
    today = date.today().isoformat()
    db.kv_set("FRED:eco_calendar", json.dumps([{"release_id": 50, "date": today}]))
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.kv_set("FRED:eco_meta", json.dumps({"PAYEMS": {"last_updated": yesterday + " 08:00:00-05"}}))
    row = next(r for r in an.eco()["rows"] if r.get("series") == "PAYEMS")
    assert row["status"] == "AWAITING FRED" and row["latest"] is None and row["prior"] == 10
    db.kv_set("FRED:eco_meta", json.dumps({"PAYEMS": {"last_updated": today + " 08:31:00-05"}}))
    row = next(r for r in an.eco()["rows"] if r.get("series") == "PAYEMS")
    assert row["status"] == "RELEASED" and row["latest"] == 10


def test_curve_and_spreads(tmp_path):
    db, p, an = _an(tmp_path)
    d = (date.today() - timedelta(days=3)).isoformat()
    obs = [Observation(entity=e, metric="yield", value=v, unit="%", observation_date=d, source="FRED",
                       release_cadence="daily") for e, v in (("UST2Y", 4.0), ("UST10Y", 4.5), ("UST3M", 4.2))]
    p.ingest("FRED", AdapterResult(observations=obs))
    c = an.curve()
    s = {x["name"]: x for x in c["spreads"]}
    assert s["2s10s"]["CURRENT"] == 50.0 and s["3m10y"]["CURRENT"] == 30.0
    assert s["5s30s"]["CURRENT"] is None            # missing tenors stay empty, never interpolated
