"""World Bank WDI adapter  (free, no key) -> annual GDP growth, CPI, current account.

Plug-in point: add indicators to INDICATORS. Aggregates (regions/income groups)
are dropped by keeping only ISO3 codes present in the country geometry.
"""
from datetime import date

from ..geo import COUNTRIES
from ..httpclient import ProviderError, require
from ..schema import AdapterResult, Observation
from .base import Adapter

INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": ("gdp_growth", "%"),
    "FP.CPI.TOTL.ZG": ("cpi", "%"),
    "BN.CAB.XOKA.GD.ZS": ("current_account", "% GDP"),
}


class WorldBankAdapter(Adapter):
    name = "WORLDBANK"
    free = True
    description = "World Bank WDI: annual GDP growth, CPI inflation, current account (% GDP)"
    signup_url = "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392 (no key)"

    async def poll(self) -> AdapterResult:
        res = AdapterResult()
        y1 = date.today().year
        y0 = y1 - int(self.cfg.get("backfill_years", 10))
        ok = 0
        for code, (metric, unit) in INDICATORS.items():
            try:
                js = await self.http.get_json(
                    f"https://api.worldbank.org/v2/country/all/indicator/{code}",
                    params={"format": "json", "per_page": 20000, "date": f"{y0}:{y1}"})
                require(isinstance(js, list) and len(js) == 2 and isinstance(js[1], list),
                        f"WB {code}: expected [meta, rows]")
            except ProviderError as exc:
                res.partial_errors += 1
                res.notes.append(f"{code}: {exc}")
                continue
            ok += 1
            for r in js[1]:
                iso3 = r.get("countryiso3code")
                if iso3 not in COUNTRIES.by_iso3 or r.get("value") is None:
                    continue
                res.observations.append(Observation(
                    entity=iso3, metric=metric, value=float(r["value"]), unit=unit,
                    observation_date=f"{r['date']}-12-31", source="WORLDBANK", release_cadence="annual",
                    period_label=str(r["date"]), flags=[f"WB:{code}"]))
        if ok == 0:
            raise ProviderError("all World Bank indicators failed")
        return res
