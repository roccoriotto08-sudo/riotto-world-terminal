"""DEMO adapter: SYNTHETIC data. Runs only when DEMO_MODE=true.

Every observation is tagged source=DEMO and flag DEMO, and the UI shows a
permanent banner. It covers only the KPI strip and watchlist prices (the keyed
feeds); keyless real feeds keep running alongside it.
"""
import math
import random
from datetime import date, datetime, timedelta, timezone

from ..schema import AdapterResult, Observation
from ..settings import SETTINGS
from .base import Adapter

_START = {"WTI": 70, "BRENT": 74, "TTF": 11, "USD_INDEX": 120, "UST10Y": 4.1, "US2S10S": 0.4, "VIX": 17,
          "HY_OAS": 3.1, "COPPER": 9800, "GOLD": 3000, "USDJPY": 148, "SPX": 6000, "HH": 3.2,
          "JKM_PROXY": 12, "URANIUM": 75}


class DemoAdapter(Adapter):
    name = "DEMO"
    free = True
    realtime = True
    description = "SYNTHETIC random-walk data for UI testing (DEMO_MODE only)"

    def __init__(self, db):
        super().__init__(db)
        self.level = {}

    def enabled(self):
        return (True, "") if SETTINGS.demo_mode else (False, "DEMO_MODE is off")

    def _entities(self):
        out = {}
        for k in SETTINGS.kpis["kpis"] + SETTINGS.kpis.get("commodities", []):
            out[k["entity"]] = (k["metric"], k["unit"])
        for h in SETTINGS.watchlist.get("holdings", []):
            out[f"EQ:{h['ticker']}"] = ("price", "USD")
        return out

    async def poll(self) -> AdapterResult:
        res = AdapterResult()
        rnd = random.Random()
        now = datetime.now(timezone.utc)
        for ent, (metric, unit) in self._entities().items():
            base = _START.get(ent, 100.0)
            vol = 0.004 if unit in ("%",) else 0.007
            if ent not in self.level:
                # 400 days of synthetic daily history so sparklines and 1D/1W/1M render
                lvl, d = base, date.today() - timedelta(days=400)
                while d < date.today():
                    lvl *= math.exp(rnd.gauss(0, vol))
                    res.observations.append(Observation(entity=ent, metric=metric, value=lvl, unit=unit,
                                                        observation_date=d.isoformat(), source="DEMO",
                                                        release_cadence="daily", flags=["DEMO"]))
                    d += timedelta(days=1)
                self.level[ent] = lvl
            self.level[ent] *= math.exp(rnd.gauss(0, vol / 6))
            res.observations.append(Observation(entity=ent, metric=metric, value=self.level[ent], unit=unit,
                                                observation_date=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                source="DEMO", release_cadence="intraday", flags=["DEMO"]))
        return res
