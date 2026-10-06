"""IMF PortWatch chokepoints  (free, no key) -> daily transit counts via AIS.

ArcGIS FeatureServer, 1000 rows/page. Data typically lags ~1 week, so the UI
always shows the observation date, never "now".
"""
from datetime import date, timedelta

from ..httpclient import require
from ..schema import AdapterResult, Observation
from ..settings import SETTINGS
from .base import Adapter

URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "Daily_Chokepoints_Data/FeatureServer/0/query")


class PortWatchAdapter(Adapter):
    name = "PORTWATCH"
    free = True
    description = "IMF PortWatch: daily chokepoint transits (all / tanker) and tanker capacity"
    signup_url = "https://portwatch.imf.org (no key)"

    async def poll(self) -> AdapterResult:
        name_to_id = {c["portwatch_name"]: c["id"] for c in SETTINGS.chokepoints}
        days = int(self.cfg.get("backfill_days", 400)) if self.first_run() else 45
        start = (date.today() - timedelta(days=days)).isoformat()
        names = ",".join(f"'{n}'" for n in name_to_id)
        where = f"date >= DATE '{start}' AND portname IN ({names})"
        res = AdapterResult()
        offset = 0
        while True:
            js = await self.http.get_json(URL, params={
                "where": where, "outFields": "date,portname,n_total,n_tanker,capacity_tanker",
                "orderByFields": "date ASC", "resultOffset": offset, "resultRecordCount": 1000, "f": "json"})
            require(isinstance(js, dict) and isinstance(js.get("features"), list),
                    f"PortWatch: {js.get('error') if isinstance(js, dict) else 'bad payload'}")
            for f in js["features"]:
                a = f.get("attributes", {})
                cid = name_to_id.get(a.get("portname"))
                d = a.get("date")
                if not cid or not d:
                    continue
                if isinstance(d, (int, float)):   # epoch ms on some layer versions
                    d = date.fromtimestamp(d / 1000).isoformat()
                for field, metric, unit in (("n_total", "transits_total", "vessels/day"),
                                            ("n_tanker", "transits_tanker", "vessels/day"),
                                            ("capacity_tanker", "capacity_tanker", "t")):
                    if a.get(field) is None:
                        continue
                    res.observations.append(Observation(
                        entity=cid, metric=metric, value=float(a[field]), unit=unit,
                        observation_date=str(d)[:10], source="PORTWATCH", release_cadence="daily",
                        flags=["IMF:PortWatch"]))
            if not js.get("exceededTransferLimit") and len(js["features"]) < 1000:
                break
            offset += 1000
        self.mark_backfilled(res)
        return res
