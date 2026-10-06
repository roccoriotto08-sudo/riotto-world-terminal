"""FRED adapter  (free, key required)  -> KPIs, FX vs USD, OECD 10Y yields.

Plug-in point: series map lives in config/fred.yaml. FRED's documented limit is
120 requests/min per key; we self-limit to 100.
"""
import json
from collections import defaultdict
from datetime import date, timedelta

from ..httpclient import ProviderError, require
from ..schema import AdapterResult, Observation
from ..settings import SETTINGS
from .base import Adapter

URL = "https://api.stlouisfed.org/fred/series/observations"
REL_URL = "https://api.stlouisfed.org/fred/release/dates"
SERIES_URL = "https://api.stlouisfed.org/fred/series"


class FredAdapter(Adapter):
    name = "FRED"
    free = True
    requires = ["fred_key"]
    description = "St. Louis Fed: Treasury yields, 2s10s, HY OAS, VIX, FX, WTI/Brent/HH daily closes, IMF monthly commodity prices, OECD 10Y"
    signup_url = "https://fred.stlouisfed.org/docs/api/api_key.html"

    async def poll(self) -> AdapterResult:
        by_id = defaultdict(list)
        for s in SETTINGS.fred["series"]:
            if SETTINGS.public_mode and s.get("license") == "restricted":
                continue        # not redistributable: never fetched on a public deployment
            by_id[s["id"]].append(s)
        years = self.cfg.get("backfill_years", 6)
        res = AdapterResult()
        ok = 0
        for sid, targets in by_id.items():
            # per-series backfill, so series added to fred.yaml later still get full history
            fresh = self.db.kv_get(f"FRED:bf:{sid}") is None
            start = (date.today() - timedelta(days=365 * years if fresh else 120)).isoformat()
            try:
                js = await self.http.get_json(URL, params={
                    "series_id": sid, "api_key": SETTINGS.fred_key, "file_type": "json",
                    "observation_start": start})
                require(isinstance(js, dict) and isinstance(js.get("observations"), list),
                        f"FRED {sid}: missing observations[]")
            except ProviderError as exc:
                res.partial_errors += 1
                res.notes.append(f"{sid}: {exc}")
                continue
            ok += 1
            res.kv[f"FRED:bf:{sid}"] = "1"
            for row in js["observations"]:
                raw = row.get("value")
                if raw in (None, ".", ""):
                    continue        # FRED marks missing days with "." - never forward-filled
                v = float(raw)
                for t in targets:
                    flags = []
                    val = v
                    if t.get("invert"):
                        if v == 0:
                            continue
                        val, flags = 1.0 / v, ["INVERTED"]
                    res.observations.append(Observation(
                        entity=t["entity"], metric=t["metric"], value=val, unit=t["unit"],
                        observation_date=row["date"], source="FRED", release_cadence=t["cadence"],
                        flags=flags + [f"FRED:{sid}"], period_label=_period(row["date"], t["cadence"])))
        if ok == 0:
            raise ProviderError("all FRED series failed: " + "; ".join(res.notes[:3]))
        await self._eco_calendar(res)
        self.mark_backfilled(res)
        return res

    async def _eco_calendar(self, res: AdapterResult) -> None:
        """ECO: scheduled release dates per release + each indicator's FRED last_updated
        stamp (tells 'released and in FRED' apart from 'released, FRED not updated yet')."""
        eco = SETTINGS.eco
        start = (date.today() - timedelta(days=eco.get("window_past_days", 10))).isoformat()
        end = (date.today() + timedelta(days=eco.get("window_future_days", 45))).isoformat()
        cal, meta = [], {}
        for rel in eco.get("releases", []):
            try:
                js = await self.http.get_json(REL_URL, params={
                    "release_id": rel["release_id"], "api_key": SETTINGS.fred_key, "file_type": "json",
                    "realtime_start": start, "realtime_end": end,
                    "include_release_dates_with_no_data": "true", "sort_order": "asc"})
                require(isinstance(js.get("release_dates"), list), f"FRED release {rel['release_id']}: no release_dates")
                cal += [{"release_id": rel["release_id"], "date": d["date"]} for d in js["release_dates"]]
            except ProviderError as exc:
                res.partial_errors += 1
                res.notes.append(f"release {rel['release_id']}: {exc}")
        for sid in sorted({i["series"] for r in eco.get("releases", []) for i in r["indicators"]}):
            try:
                js = await self.http.get_json(SERIES_URL, params={"series_id": sid, "api_key": SETTINGS.fred_key,
                                                                  "file_type": "json"})
                s = (js.get("seriess") or [{}])[0]
                meta[sid] = {"last_updated": s.get("last_updated"), "title": s.get("title"),
                             "units": s.get("units"), "seasonal": s.get("seasonal_adjustment_short")}
            except ProviderError as exc:
                res.partial_errors += 1
                res.notes.append(f"meta {sid}: {exc}")
        if cal:
            res.kv["FRED:eco_calendar"] = json.dumps(cal)
        if meta:
            res.kv["FRED:eco_meta"] = json.dumps(meta)


def _period(d: str, cadence: str):
    if cadence == "monthly":
        return d[:7]
    if cadence == "quarterly":
        return f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}"
    return None
