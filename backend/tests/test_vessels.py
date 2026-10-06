from datetime import datetime, timedelta, timezone

from backend.app.db import DB
from backend.app.vessels import VesselStore, decode_ais_eta, flag_of, iso, laden_guess, size_class


def _store(tmp_path):
    st = VesselStore(DB(tmp_path / "t.db"))
    st.upsert_static([{"mmsi": 538000001, "name": "TEST VLCC", "ship_type": 80, "length": 333, "beam": 60,
                       "draught": 21.0, "ts": iso(datetime.now(timezone.utc)), "source": "T"},
                      {"mmsi": 211000001, "name": "CARGO", "ship_type": 70, "ts": iso(datetime.now(timezone.utc)), "source": "T"}])
    return st


def pos(mmsi, lat, lon, minutes_ago, sog=12.0):
    return {"mmsi": mmsi, "lat": lat, "lon": lon, "sog": sog, "ts": iso(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)),
            "source": "T"}


def test_only_tankers_stored(tmp_path):
    st = _store(tmp_path)
    assert st.known_tankers() == {538000001}


def test_position_sanity(tmp_path):
    st = _store(tmp_path)
    assert st.upsert_positions([pos(538000001, 26.5, 56.3, 60)]) == 1
    assert st.upsert_positions([pos(538000001, 0.0, 0.0, 50)]) == 0          # null island
    assert st.upsert_positions([pos(538000001, 26.6, 56.3, 40, sog=55)]) == 0  # impossible tanker speed
    assert st.upsert_positions([pos(538000001, 51.9, 4.1, 30)]) == 0          # Hormuz -> Rotterdam in 30 min
    assert st.upsert_positions([pos(538000001, 26.55, 56.35, 20)]) == 1       # plausible move
    assert st.rejected == 3
    v = st.get(538000001)
    assert v["size_class"] == "VLCC" and v["laden"] == "LADEN?" and v["flag"] == "Marshall Is."
    assert len(v["track"]) == 2          # fixes 40 min apart (> track_min_minutes) are both kept


def test_near_and_search(tmp_path):
    st = _store(tmp_path)
    st.upsert_positions([pos(538000001, 26.5, 56.3, 10)])
    assert st.near(26.57, 56.25, 50)[0]["mmsi"] == 538000001
    assert st.near(51.9, 4.1, 50) == []
    assert st.search("vlcc")[0]["mmsi"] == 538000001


def test_eta_decode():
    ref = datetime(2026, 10, 5, tzinfo=timezone.utc)
    packed = (10 << 16) | (7 << 11) | (14 << 6) | 30
    assert decode_ais_eta(packed, ref) == "2026-10-07T14:30Z"
    assert decode_ais_eta((1 << 16) | (5 << 11), ref).startswith("2027-01-05")   # January seen in October -> next year
    assert decode_ais_eta(0, ref) is None
    assert decode_ais_eta((0 << 16) | (0 << 11) | (24 << 6) | 60, ref) is None    # AIS "not available"


def test_classes_and_flags():
    assert size_class(280)["name"] == "Suezmax" and size_class(150)["name"] == "MR"
    assert laden_guess(280, 9.6) == "BALLAST?" and laden_guess(None, 10) is None
    assert flag_of(273123456) == "Russia" and flag_of(12345) is None
