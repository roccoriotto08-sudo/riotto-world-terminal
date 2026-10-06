"""BIS central bank policy rates  (free, no key) -> daily policy rate for ~40 central banks.

Dataflow WS_CBPOL, daily frequency. XM (euro area) is stored as entity EA and
expanded to members only at display time.
"""
import csv
import io
from datetime import date, timedelta

from ..geo import COUNTRIES
from ..httpclient import ProviderError, require
from ..schema import AdapterResult, Observation
from .base import Adapter

URL = "https://stats.bis.org/api/v1/data/WS_CBPOL/D../all"


class BisAdapter(Adapter):
    name = "BIS"
    free = True
    description = "BIS WS_CBPOL: central bank policy rates (daily)"
    signup_url = "https://data.bis.org (no key)"

    async def poll(self) -> AdapterResult:
        years = int(self.cfg.get("backfill_years", 6))
        start = date.today() - (timedelta(days=365 * years) if self.first_run() else timedelta(days=45))
        text = await self.http.get_text(URL, params={"startPeriod": start.isoformat(), "format": "csv",
                                                     "detail": "dataonly"})
        reader = csv.DictReader(io.StringIO(text))
        require(reader.fieldnames is not None and {"REF_AREA", "TIME_PERIOD", "OBS_VALUE"} <= set(reader.fieldnames),
                f"BIS: unexpected columns {reader.fieldnames}")
        res = AdapterResult()
        # BIS daily series repeat the rate every calendar day; keep change points
        # plus month-ends so history stays compact but exact.
        last = {}
        rows = sorted(reader, key=lambda r: (r["REF_AREA"], r["TIME_PERIOD"]))
        for i, r in enumerate(rows):
            area = r["REF_AREA"]
            entity = "EA" if area == "XM" else COUNTRIES.iso2_to_iso3.get(area)
            if not entity or r["OBS_VALUE"] in ("", "NaN"):
                continue
            v = float(r["OBS_VALUE"])
            d = r["TIME_PERIOD"]
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            is_last_of_area = nxt is None or nxt["REF_AREA"] != area
            month_end = nxt is None or nxt["TIME_PERIOD"][:7] != d[:7]
            if last.get(entity) == v and not (month_end or is_last_of_area):
                continue
            last[entity] = v
            res.observations.append(Observation(
                entity=entity, metric="policy_rate", value=v, unit="%", observation_date=d,
                source="BIS", release_cadence="daily", flags=["BIS:WS_CBPOL"]))
        if not res.observations:
            raise ProviderError("BIS returned no policy-rate rows")
        self.mark_backfilled(res)
        return res
