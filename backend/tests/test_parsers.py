"""Parser/schema tests on minimal fixture payloads shaped like the real providers."""
import io
import zipfile

import pytest

from backend.app.adapters.gdelt import parse_export
from backend.app.alerts import annotate_event
from backend.app.httpclient import SchemaError
from backend.app.schema import GeoEvent, Observation


def gdelt_row(**kw):
    row = [""] * 61
    row[0] = kw.get("id", "1")
    row[6], row[16] = kw.get("a1", "ISRAEL"), kw.get("a2", "MILITANT")
    row[12] = kw.get("t1", "MIL")
    row[26], row[27], row[28] = kw.get("code", "190"), kw.get("base", "190"), kw.get("root", "19")
    row[30], row[32], row[33] = kw.get("gold", "-10"), kw.get("src", "12"), kw.get("art", "40")
    row[52], row[53] = kw.get("place", "Gaza, Gaza Strip"), kw.get("fips", "IS")
    row[56], row[57] = kw.get("lat", "31.5"), kw.get("lon", "34.47")
    row[59], row[60] = "20261005221500", "https://example.org/a"
    return "\t".join(row)


def zipped(lines):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.export.CSV", "\n".join(lines))
    return buf.getvalue()


def test_gdelt_parses_and_grades():
    ev = parse_export(zipped([gdelt_row()]))
    assert len(ev) == 1
    e = ev[0]
    assert e.category == "CONFLICT" and e.country == "ISR" and 1 <= e.severity <= 4
    assert e.ts == "2026-10-05T22:15:00Z"


def test_gdelt_drops_crime_without_armed_actor():
    assert parse_export(zipped([gdelt_row(t1="")])) == []


def test_gdelt_drops_low_coverage():
    assert parse_export(zipped([gdelt_row(src="1", art="2")])) == []


def test_gdelt_wrong_width_is_schema_error():
    with pytest.raises(SchemaError):
        parse_export(zipped(["a\tb\tc"]))


def test_observation_rejects_nan_and_bad_date():
    with pytest.raises(ValueError):
        Observation(entity="X", metric="m", value=float("nan"), unit="%", observation_date="2026-01-01",
                    source="T", release_cadence="daily")
    with pytest.raises(ValueError):
        Observation(entity="X", metric="m", value=1.0, unit="%", observation_date="01/02/2026",
                    source="T", release_cadence="daily")


def test_readthrough_map_applied():
    e = annotate_event(GeoEvent(id="t", category="CONFLICT", title="x", country="IRN", severity=4,
                                ts="2026-10-05T00:00:00Z", source="TEST"))
    assert "OIL" in e.commodities and e.readthrough["assets"][0]["asset"] == "BRENT"


def test_chokepoint_proximity_readthrough():
    # at sea near Bab el-Mandeb, no country
    e = annotate_event(GeoEvent(id="t", category="CONFLICT", title="x", lat=12.9, lon=43.2, severity=4,
                                ts="2026-10-05T00:00:00Z", source="TEST"))
    assert e.entity == "BAB" and e.readthrough is not None
