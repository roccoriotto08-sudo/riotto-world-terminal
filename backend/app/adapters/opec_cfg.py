"""OPEC+ quotas from config/reference/opec_quotas.yaml (manual, user-maintained).

There is no free machine-readable OPEC feed (the MOMR is a PDF). Plug-in point:
replace this adapter with a licensed feed (e.g. Argus/Platts/Kpler) if you have one.
"""
import re

from ..geo import COUNTRIES
from ..httpclient import require
from ..schema import AdapterResult, Observation
from ..settings import SETTINGS
from .base import Adapter


class OpecConfigAdapter(Adapter):
    name = "OPEC_CFG"
    free = True
    description = "OPEC+ required production (manual config; MOMR has no API)"
    signup_url = "https://www.opec.org/opec_web/en/publications/338.htm"

    async def poll(self) -> AdapterResult:
        res = AdapterResult()
        for q in SETTINGS.reload_opec_quotas():
            require(q.get("iso3") in COUNTRIES.by_iso3, f"opec_quotas: bad iso3 {q.get('iso3')}")
            require(bool(re.match(r"^\d{4}-\d{2}$", str(q.get("month", "")))), f"opec_quotas: bad month {q}")
            require(str(q.get("source", "")).startswith("http"), f"opec_quotas: source URL required {q}")
            res.observations.append(Observation(
                entity=q["iso3"], metric="opec_quota", value=float(q["quota_kbd"]), unit="kb/d",
                observation_date=f"{q['month']}-01", source="OPEC_CFG", release_cadence="monthly",
                period_label=q["month"], flags=["MANUAL", f"src:{q['source']}"]))
        if not res.observations:
            res.notes.append("no quotas configured")
        return res
