"""AIS tanker store: positions, static data, sampled tracks.

Positions are not time-series observations, so they bypass the Observation pipeline,
but get the same discipline: sanity checks (impossible coordinates / speeds / jumps are
rejected and counted), provenance (source + report time) and staleness on read.
"""
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .settings import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS vessels (
  mmsi INTEGER PRIMARY KEY, name TEXT, imo INTEGER, callsign TEXT, ship_type INTEGER,
  length REAL, beam REAL, draught REAL, destination TEXT, eta TEXT,
  lat REAL, lon REAL, sog REAL, cog REAL, heading REAL, nav_status INTEGER,
  pos_ts TEXT, static_ts TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS ix_vessels_pos ON vessels(pos_ts);
CREATE TABLE IF NOT EXISTS vessel_track (
  mmsi INTEGER, ts TEXT, lat REAL, lon REAL, sog REAL, source TEXT, PRIMARY KEY (mmsi, ts)
);
"""

NAV_STATUS = {0: "Under way (engine)", 1: "At anchor", 2: "Not under command", 3: "Restricted manoeuvrability",
              4: "Constrained by draught", 5: "Moored", 6: "Aground", 7: "Fishing", 8: "Under way (sail)",
              11: "Towing astern", 12: "Pushing/towing", 14: "AIS-SART", 15: "Not defined"}

# MMSI Maritime Identification Digits -> flag (subset covering the tanker registries that matter).
MID = {}
for _mids, _flag in [((636, 637), "Liberia"), ((538,), "Marshall Is."), ((351, 352, 353, 354, 355, 356, 357, 370, 371, 372, 373, 374), "Panama"),
                     ((215, 229, 248, 249, 256), "Malta"), ((563, 564, 565, 566), "Singapore"), ((477,), "Hong Kong"),
                     ((237, 239, 240, 241), "Greece"), ((308, 309, 311), "Bahamas"), ((209, 210, 212), "Cyprus"),
                     ((412, 413, 414), "China"), ((273,), "Russia"), ((422,), "Iran"), ((403,), "Saudi Arabia"),
                     ((470, 471), "UAE"), ((447,), "Kuwait"), ((466,), "Qatar"), ((425,), "Iraq"), ((461,), "Oman"),
                     ((408,), "Bahrain"), ((338, 366, 367, 368, 369), "USA"), ((232, 233, 234, 235), "UK"),
                     ((244, 245, 246), "Netherlands"), ((257, 258, 259), "Norway"), ((265, 266), "Sweden"),
                     ((219, 220), "Denmark"), ((230,), "Finland"), ((276,), "Estonia"), ((275,), "Latvia"),
                     ((277,), "Lithuania"), ((261,), "Poland"), ((211, 218), "Germany"), ((226, 227, 228), "France"),
                     ((247,), "Italy"), ((224, 225), "Spain"), ((271,), "Türkiye"), ((431, 432), "Japan"),
                     ((440, 441), "S. Korea"), ((419,), "India"), ((533,), "Malaysia"), ((525,), "Indonesia"),
                     ((548,), "Philippines"), ((574,), "Vietnam"), ((567,), "Thailand"), ((416,), "Taiwan"),
                     ((620, 616), "Comoros"), ((667,), "Sierra Leone"), ((572,), "Tuvalu"), ((677,), "Tanzania"),
                     ((341,), "St Kitts & Nevis"), ((312,), "Belize"), ((613,), "Cameroon"), ((626,), "Gabon"),
                     ((511,), "Palau"), ((671,), "Togo"), ((375, 376, 377), "St Vincent"), ((305,), "Antigua"),
                     ((314,), "Barbados"), ((518,), "Cook Is."), ((775,), "Venezuela"), ((710,), "Brazil"),
                     ((345,), "Mexico"), ((316,), "Canada"), ((622,), "Egypt"), ((657,), "Nigeria"),
                     ((601,), "South Africa"), ((603,), "Angola"), ((605,), "Algeria"), ((642,), "Libya"),
                     ((405,), "Bangladesh"), ((445,), "N. Korea"), ((668,), "São Tomé"), ((457,), "Mongolia"),
                     ((273,), "Russia"), ((214,), "Moldova"), ((272,), "Ukraine"), ((428,), "Israel"), ((450,), "Lebanon"), ((205,), "Belgium"), ((263,), "Portugal"),
                     ((255,), "Madeira (PT)"), ((236,), "Gibraltar"), ((231,), "Faroe Is."), ((251,), "Iceland")]:
    for _m in _mids:
        MID[_m] = _flag


def flag_of(mmsi: int) -> Optional[str]:
    s = str(mmsi)
    return MID.get(int(s[:3])) if len(s) == 9 else None


def is_tanker(ship_type) -> bool:
    return ship_type is not None and 80 <= int(ship_type) <= 89


def size_class(length: Optional[float]) -> Optional[dict]:
    if not length:
        return None
    for c in SETTINGS.ais["classes"]:
        if length >= c["min_len"]:
            return c
    return None


def laden_guess(length, draught) -> Optional[str]:
    """Heuristic only: reported draught vs a typical laden draught for the size class."""
    c = size_class(length)
    if not c or not draught:
        return None
    return "LADEN?" if draught >= c["laden_draught_m"] else "BALLAST?"


def decode_ais_eta(packed: Optional[int], ref: datetime) -> Optional[str]:
    """AIS packs ETA as month<<16 | day<<11 | hour<<6 | minute (no year). 0/24/60 = n/a."""
    if not packed:
        return None
    mo, d, h, mi = (packed >> 16) & 0xF, (packed >> 11) & 0x1F, (packed >> 6) & 0x1F, packed & 0x3F
    return eta_str(mo, d, h, mi, ref)


def eta_str(mo, d, h, mi, ref: datetime) -> Optional[str]:
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60):
        return None
    year = ref.year + (1 if mo < ref.month - 6 else 0)   # ETA in January seen in November -> next year
    try:
        return datetime(year, mo, d, h, mi, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    except ValueError:
        return None


def km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, a)))


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


class VesselStore:
    def __init__(self, db):
        self.db = db
        with db.lock:
            db.conn.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.rejected = 0
        self._last_track: Dict[int, str] = {}

    # ---------------------------------------------------------------- sanity
    def valid_position(self, mmsi: int, lat, lon, sog, ts: str) -> bool:
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False
        if abs(lat) < 0.01 and abs(lon) < 0.01:        # "null island" = no GPS fix
            return False
        if sog is not None and 40 < sog < 102.2:       # 102.3 = "not available"; tankers do ~15 kn
            return False
        prev = self.db._q("SELECT lat, lon, pos_ts FROM vessels WHERE mmsi=?", (mmsi,))
        if prev and prev[0]["lat"] is not None and prev[0]["pos_ts"]:
            hours = (parse_iso(ts) - parse_iso(prev[0]["pos_ts"])).total_seconds() / 3600
            if hours > 0:
                if km(prev[0]["lat"], prev[0]["lon"], lat, lon) / hours > 60 * 1.852:   # implied > 60 kn: teleport
                    return False
            elif hours < 0:
                return False                            # older than what we hold
        return True

    # ---------------------------------------------------------------- writes
    def upsert_static(self, rows: List[dict]) -> int:
        n = 0
        with self.db.lock:
            for r in rows:
                if not is_tanker(r.get("ship_type")):
                    continue
                self.db.conn.execute(
                    "INSERT INTO vessels (mmsi, name, imo, callsign, ship_type, length, beam, draught, destination, eta, static_ts, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mmsi) DO UPDATE SET name=excluded.name, imo=excluded.imo, "
                    "callsign=excluded.callsign, ship_type=excluded.ship_type, length=excluded.length, beam=excluded.beam, "
                    "draught=excluded.draught, destination=excluded.destination, eta=excluded.eta, static_ts=excluded.static_ts",
                    (r["mmsi"], r.get("name"), r.get("imo") or None, r.get("callsign"), r["ship_type"], r.get("length") or None,
                     r.get("beam") or None, r.get("draught") or None, r.get("destination"), r.get("eta"), r["ts"], r["source"]))
                n += 1
        return n

    def known_tankers(self) -> set:
        return {r["mmsi"] for r in self.db._q("SELECT mmsi FROM vessels")}

    def upsert_positions(self, rows: List[dict]) -> int:
        """rows: mmsi, lat, lon, sog, cog, heading, nav_status, ts, source. Tankers only (caller filters)."""
        tmin = SETTINGS.ais.get("track_min_minutes", 15)
        n = 0
        for r in rows:
            if not self.valid_position(r["mmsi"], r["lat"], r["lon"], r.get("sog"), r["ts"]):
                self.rejected += 1
                continue
            sog = r.get("sog") if r.get("sog") is not None and r.get("sog") < 102.2 else None
            hdg = r.get("heading") if r.get("heading") is not None and r.get("heading") < 360 else None
            with self.db.lock:
                self.db.conn.execute(
                    "UPDATE vessels SET lat=?, lon=?, sog=?, cog=?, heading=?, nav_status=?, pos_ts=?, source=? "
                    "WHERE mmsi=? AND (pos_ts IS NULL OR pos_ts<=?)",
                    (r["lat"], r["lon"], sog, r.get("cog"), hdg, r.get("nav_status"), r["ts"], r["source"], r["mmsi"], r["ts"]))
                last = self._last_track.get(r["mmsi"])
                if last is None or (parse_iso(r["ts"]) - parse_iso(last)) >= timedelta(minutes=tmin):
                    self.db.conn.execute("INSERT OR IGNORE INTO vessel_track VALUES (?,?,?,?,?,?)",
                                         (r["mmsi"], r["ts"], r["lat"], r["lon"], sog, r["source"]))
                    self._last_track[r["mmsi"]] = r["ts"]
            n += 1
        return n

    def prune(self) -> None:
        cut = iso(datetime.now(timezone.utc) - timedelta(days=SETTINGS.ais.get("track_retention_days", 10)))
        with self.db.lock:
            self.db.conn.execute("DELETE FROM vessel_track WHERE ts<?", (cut,))

    # ----------------------------------------------------------------- reads
    def _shape(self, r) -> dict:
        d = dict(r)
        now = datetime.now(timezone.utc)
        age = (now - parse_iso(d["pos_ts"])).total_seconds() if d.get("pos_ts") else None
        c = size_class(d.get("length"))
        d.update(age_s=int(age) if age is not None else None,
                 stale=age is None or age > SETTINGS.ais.get("stale_after_minutes", 120) * 60,
                 size_class=c["name"] if c else None, laden=laden_guess(d.get("length"), d.get("draught")),
                 flag=flag_of(d["mmsi"]), nav_text=NAV_STATUS.get(d.get("nav_status")),
                 moving=(d.get("sog") or 0) >= 1.0)
        return d

    def active(self) -> List[dict]:
        cut = iso(datetime.now(timezone.utc) - timedelta(hours=SETTINGS.ais.get("drop_after_hours", 72)))
        rows = self.db._q("SELECT * FROM vessels WHERE lat IS NOT NULL AND pos_ts>=?", (cut,))
        return [self._shape(r) for r in rows]

    def get(self, mmsi: int, track_hours: int = 72) -> Optional[dict]:
        rows = self.db._q("SELECT * FROM vessels WHERE mmsi=?", (mmsi,))
        if not rows:
            return None
        d = self._shape(rows[0])
        since = iso(datetime.now(timezone.utc) - timedelta(hours=track_hours))
        d["track"] = [dict(t) for t in self.db._q(
            "SELECT ts, lat, lon, sog FROM vessel_track WHERE mmsi=? AND ts>=? ORDER BY ts", (mmsi, since))]
        return d

    def near(self, lat: float, lon: float, radius_km: float) -> List[dict]:
        out = []
        for v in self.active():
            dist = km(lat, lon, v["lat"], v["lon"])
            if dist <= radius_km:
                out.append(dict(v, dist_km=round(dist, 1)))
        return sorted(out, key=lambda x: x["dist_km"])

    def search(self, q: str) -> List[dict]:
        q = q.strip().upper()
        if q.isdigit():
            rows = self.db._q("SELECT * FROM vessels WHERE mmsi=? OR imo=?", (int(q), int(q)))
        else:
            rows = self.db._q("SELECT * FROM vessels WHERE UPPER(name) LIKE ? LIMIT 20", (f"%{q}%",))
        return [self._shape(r) for r in rows]


_STORES: Dict[int, VesselStore] = {}


def store_for(db) -> VesselStore:
    if id(db) not in _STORES:
        _STORES[id(db)] = VesselStore(db)
    return _STORES[id(db)]
