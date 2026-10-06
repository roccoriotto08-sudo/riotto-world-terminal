"""GDACS (UN/EC Global Disaster Alert and Coordination System)  (free, no key)
-> orange/red alerts for cyclones, floods, earthquakes, droughts, wildfires, volcanoes.
Fills the WEATHER / DISASTER part of the geopolitical layer."""
from datetime import date, timedelta

from ..httpclient import require
from ..schema import AdapterResult, GeoEvent
from .base import Adapter

URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
TYPES = {"TC": ("WEATHER", "Tropical cyclone"), "FL": ("WEATHER", "Flood"), "DR": ("WEATHER", "Drought"),
         "WF": ("WEATHER", "Wildfire"), "EQ": ("DISASTER", "Earthquake"), "VO": ("DISASTER", "Volcano")}


class GdacsAdapter(Adapter):
    name = "GDACS"
    free = True
    description = "GDACS: orange/red natural-hazard alerts (cyclones, floods, quakes, drought)"
    signup_url = "https://www.gdacs.org (no key)"

    async def poll(self) -> AdapterResult:
        days = int(self.cfg.get("backfill_days", 365)) if self.first_run() else 30
        js = await self.http.get_json(URL, params={
            "eventlist": ";".join(TYPES), "alertlevel": "Orange;Red",
            "fromdate": (date.today() - timedelta(days=days)).isoformat(), "todate": date.today().isoformat()})
        require(isinstance(js, dict) and isinstance(js.get("features"), list), "GDACS: missing features[]")
        res = AdapterResult()
        for f in js["features"]:
            p, g = f.get("properties", {}), f.get("geometry") or {}
            et = p.get("eventtype")
            if et not in TYPES or g.get("type") != "Point":
                continue
            cat, label = TYPES[et]
            level = (p.get("episodealertlevel") or p.get("alertlevel") or "").lower()
            sev = 4 if level == "red" else 3
            if level == "red" and et in ("TC", "EQ"):
                sev = 5
            ts = (p.get("todate") if et == "DR" else p.get("fromdate")) or p.get("datemodified")
            sevtxt = (p.get("severitydata") or {}).get("severitytext", "")
            countries = (p.get("country") or "").strip(" ,")
            if len(countries) > 60:
                countries = countries[:57].rsplit(",", 1)[0] + "…"
            res.events.append(GeoEvent(
                id=f"GDACS:{et}{p.get('eventid')}:{p.get('episodeid')}", category=cat,
                title=f"{label} ({level.upper()}): {p.get('name') if et != 'DR' else countries} — {sevtxt}"[:220],
                lat=float(g["coordinates"][1]), lon=float(g["coordinates"][0]), country=p.get("iso3") or None,
                severity=sev, ts=ts[:19] + "Z", source="GDACS",
                url=(p.get("url") or {}).get("report")))
        self.mark_backfilled(res)
        return res
