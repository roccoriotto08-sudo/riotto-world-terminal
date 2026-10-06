"""Digitraffic Marine (Fintraffic, Finland)  (free, no key) -> live AIS for the Baltic /
Gulf of Finland, including the Primorsk and Ust-Luga crude export lanes.

Two REST calls per poll: vessel static data (name, IMO, ship type, dimensions, draught in
decimetres, destination, packed ETA) and latest positions (GeoJSON). Only AIS types 80-89
are stored. Licence: CC BY 4.0, attribution "Fintraffic / digitraffic.fi".
"""
from datetime import datetime, timedelta, timezone

from ..httpclient import require
from ..schema import AdapterResult
from ..vessels import decode_ais_eta, is_tanker, iso, store_for
from .base import Adapter

BASE = "https://meri.digitraffic.fi/api/ais/v1"
HEADERS = {"Digitraffic-User": "riotto-world-terminal/0.1", "Accept-Encoding": "gzip"}


def _ts(ms) -> str:
    return iso(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


class DigitrafficAdapter(Adapter):
    name = "DIGITRAFFIC"
    free = True
    realtime = True
    description = "Digitraffic (Fintraffic) live AIS: Baltic tankers incl. Primorsk / Ust-Luga lanes"
    signup_url = "https://www.digitraffic.fi/en/marine-traffic/ (no key)"

    async def poll(self) -> AdapterResult:
        store = store_for(self.db)
        since = int((datetime.now(timezone.utc) - timedelta(hours=72)).timestamp() * 1000)
        vessels = await self.http.get_json(f"{BASE}/vessels", params={"from": since}, headers=HEADERS)
        require(isinstance(vessels, list), "Digitraffic /vessels: expected a list")
        now = datetime.now(timezone.utc)
        static = []
        for v in vessels:
            if not is_tanker(v.get("shipType")):
                continue
            a, b, c, d = (v.get(k) or 0 for k in ("referencePointA", "referencePointB", "referencePointC", "referencePointD"))
            static.append({"mmsi": v["mmsi"], "name": (v.get("name") or "").strip() or None, "imo": v.get("imo"),
                           "callsign": v.get("callSign"), "ship_type": v["shipType"],
                           "length": (a + b) or None, "beam": (c + d) or None,
                           "draught": (v["draught"] / 10.0) if v.get("draught") else None,   # decimetres -> m
                           "destination": (v.get("destination") or "").strip() or None,
                           "eta": decode_ais_eta(v.get("eta"), now), "ts": _ts(v["timestamp"]), "source": "DIGITRAFFIC"})
        n_static = await self._thread(store.upsert_static, static)
        locs = await self.http.get_json(f"{BASE}/locations", params={"from": since}, headers=HEADERS)
        require(isinstance(locs, dict) and isinstance(locs.get("features"), list), "Digitraffic /locations: no features")
        tankers = {s["mmsi"] for s in static} | store.known_tankers()
        pos = []
        for f in locs["features"]:
            if f.get("mmsi") not in tankers:
                continue
            p, (lon, lat) = f["properties"], f["geometry"]["coordinates"][:2]
            pos.append({"mmsi": f["mmsi"], "lat": lat, "lon": lon, "sog": p.get("sog"), "cog": p.get("cog"),
                        "heading": p.get("heading"), "nav_status": p.get("navStat"),
                        "ts": _ts(p["timestampExternal"]), "source": "DIGITRAFFIC"})
        before = store.rejected
        n_pos = await self._thread(store.upsert_positions, pos)
        await self._thread(store.prune)
        return AdapterResult(notes=[f"{n_static} tankers static, {n_pos} positions, {store.rejected - before} rejected"])

    @staticmethod
    async def _thread(fn, *a):
        import asyncio
        return await asyncio.to_thread(fn, *a)
