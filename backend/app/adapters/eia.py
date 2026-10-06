"""EIA API v2 adapter  (free, key required).

Pulls:
  * Weekly Petroleum Status Report: US field production, commercial crude stocks, SPR
  * Weekly natural gas storage (Lower 48)
  * International monthly crude production by country (cross-check vs JODI)
  * STEO OPEC member crude production (history only - forecast months are dropped)

New-release detection: the newest weekly `period` is stored in kv; when it advances
the poll result carries a "new WPSR release" note and the rules engine sees new rows.
"""
from datetime import date, timedelta

from ..geo import COUNTRIES
from ..httpclient import ProviderError, require
from ..schema import AdapterResult, Observation
from ..settings import SETTINGS
from .base import Adapter

BASE = "https://api.eia.gov/v2"

WEEKLY = [
    # route, series facet, entity, metric, unit as published
    ("petroleum/sum/sndw", "WCRFPUS2", "USA", "crude_prod_weekly", "MBBL/D"),
    ("petroleum/sum/sndw", "WCESTUS1", "USA", "crude_stocks_comm", "MBBL"),
    ("petroleum/sum/sndw", "WCSSTUS1", "USA", "crude_stocks_spr", "MBBL"),
    ("natural-gas/stor/wkly", "NW2_EPG0_SWO_R48_BCF", "USA", "gas_storage_l48", "BCF"),
]

# Top producers for the international pull (EIA uses ISO3 country ids).
INTL_COUNTRIES = ["USA", "SAU", "RUS", "CAN", "IRQ", "CHN", "ARE", "BRA", "IRN", "KWT", "KAZ", "NOR",
                  "MEX", "NGA", "QAT", "LBY", "DZA", "AGO", "OMN", "GUY", "COL", "VEN", "AZE", "GBR",
                  "ARG", "IND", "IDN", "MYS", "EGY", "ECU"]

# STEO crude production series (mb/d). IDs follow EIA STEO table 3d naming.
STEO = {"COPR_SA": "SAU", "COPR_IZ": "IRQ", "COPR_IR": "IRN", "COPR_KU": "KWT", "COPR_TC": "ARE",
        "COPR_LY": "LBY", "COPR_NI": "NGA", "COPR_VE": "VEN", "COPR_AG": "DZA"}


class EiaAdapter(Adapter):
    name = "EIA"
    free = True
    requires = ["eia_key"]
    description = "EIA v2: weekly petroleum status, US production & stocks, gas storage, international + STEO production"
    signup_url = "https://www.eia.gov/opendata/register.php"

    async def _data(self, route: str, params: dict) -> list:
        p = {"api_key": SETTINGS.eia_key, "data[0]": "value", "sort[0][column]": "period",
             "sort[0][direction]": "desc", "length": 5000}
        p.update(params)
        js = await self.http.get_json(f"{BASE}/{route}/data/", params=p)
        require(isinstance(js, dict) and "response" in js and isinstance(js["response"].get("data"), list),
                f"EIA {route}: unexpected payload keys {list(js)[:5] if isinstance(js, dict) else type(js)}")
        return js["response"]["data"]

    async def poll(self) -> AdapterResult:
        res = AdapterResult()
        years = self.cfg.get("backfill_years", 6)
        first = self.first_run()
        start_w = (date.today() - timedelta(days=365 * years if first else 120)).isoformat()
        start_m = (date.today() - timedelta(days=365 * years if first else 500)).strftime("%Y-%m")
        ok = 0

        for route, series, entity, metric, unit in WEEKLY:
            try:
                rows = await self._data(route, {"frequency": "weekly", "facets[series][]": series, "start": start_w})
            except ProviderError as exc:
                res.partial_errors += 1
                res.notes.append(f"{series}: {exc}")
                continue
            ok += 1
            latest = None
            for r in rows:
                if r.get("value") in (None, ""):
                    continue
                res.observations.append(Observation(
                    entity=entity, metric=metric, value=float(r["value"]), unit=r.get("units") or unit,
                    observation_date=r["period"], source="EIA", release_cadence="weekly", flags=[f"EIA:{series}"]))
                latest = max(latest or r["period"], r["period"])
            key = f"EIA:last_period:{series}"
            if latest and latest != self.db.kv_get(key):
                if self.db.kv_get(key):
                    res.notes.append(f"new EIA release {series} period {latest}")
                res.kv[key] = latest

        try:
            params = {"frequency": "monthly", "facets[productId][]": "57", "facets[activityId][]": "1",
                      "facets[unit][]": "TBPD", "start": start_m}
            params["facets[countryRegionId][]"] = INTL_COUNTRIES
            rows = await self._data("international", params)
            ok += 1
            for r in rows:
                iso3 = r.get("countryRegionId")
                if iso3 not in COUNTRIES.by_iso3 or r.get("value") in (None, "", "--", "NA"):
                    continue
                res.observations.append(Observation(
                    entity=iso3, metric="crude_prod", value=float(r["value"]), unit="TBPD",
                    observation_date=f"{r['period']}-01", source="EIA", release_cadence="monthly",
                    period_label=r["period"], flags=["EIA:intl"]))
        except ProviderError as exc:
            res.partial_errors += 1
            res.notes.append(f"international: {exc}")

        try:
            cutoff = date.today().replace(day=1).strftime("%Y-%m")
            rows = await self._data("steo", {"frequency": "monthly", "facets[seriesId][]": list(STEO),
                                             "start": start_m})
            ok += 1
            for r in rows:
                iso3 = STEO.get(r.get("seriesId"))
                if not iso3 or r.get("value") in (None, "") or r["period"] >= cutoff:
                    continue   # STEO forecast months are never shown as data
                res.observations.append(Observation(
                    entity=iso3, metric="crude_prod_steo", value=float(r["value"]), unit="mb/d",
                    observation_date=f"{r['period']}-01", source="EIA_STEO", release_cadence="monthly",
                    period_label=r["period"], flags=["ESTIMATE", f"STEO:{r['seriesId']}"]))
        except ProviderError as exc:
            res.partial_errors += 1
            res.notes.append(f"steo: {exc}")

        if ok == 0:
            raise ProviderError("all EIA routes failed: " + "; ".join(res.notes[:3]))
        self.mark_backfilled(res)
        return res
