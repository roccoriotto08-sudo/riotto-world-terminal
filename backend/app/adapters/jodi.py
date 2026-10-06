"""JODI Oil World Database  (free, no key) -> monthly crude production by country (kb/d).

The primary CSVs are linked from the data-downloads page; we scrape the links
instead of hard-coding them because the current-year file name differs
("primaryyear2026.csv" vs "2025.csv"). Unchanged files are skipped using
Last-Modified. ASSESSMENT_CODE 3 ("not assessed") is kept but flagged UNASSESSED.
"""
import csv
import io
import re
from datetime import date

from ..geo import COUNTRIES
from ..httpclient import ProviderError, require
from ..schema import AdapterResult, Observation
from .base import Adapter

PAGE = "https://www.jodidata.org/oil/database/data-downloads.aspx"
HOST = "https://www.jodidata.org"
LINK_RE = re.compile(r'href="(/_resources/files/downloads/oil-data/annual-csv/primary/[^"]+\.csv)"', re.I)


class JodiAdapter(Adapter):
    name = "JODI"
    free = True
    description = "JODI-Oil: monthly crude production by country (60+ countries, 1-3 month lag)"
    signup_url = "https://www.jodidata.org/oil/ (no key)"

    def _year_of(self, path: str) -> int:
        m = re.search(r"(\d{4})\.csv$", path)
        return int(m.group(1)) if m else 0

    async def poll(self) -> AdapterResult:
        html = await self.http.get_text(PAGE)
        links = sorted(set(LINK_RE.findall(html)), key=self._year_of, reverse=True)
        require(len(links) > 0, "JODI: no primary CSV links found on downloads page")
        this_year = date.today().year
        span = int(self.cfg.get("backfill_years", 6)) if self.first_run() else 1
        wanted = [l for l in links if self._year_of(l) >= this_year - span]
        res = AdapterResult()
        for path in wanted:
            url = HOST + path
            resp = await self.http.request("GET", url)
            lm = resp.headers.get("last-modified", "")
            key = f"JODI:lm:{path}"
            if lm and self.db.kv_get(key) == lm:
                continue
            reader = csv.DictReader(io.StringIO(resp.text))
            require(reader.fieldnames is not None and
                    {"REF_AREA", "TIME_PERIOD", "ENERGY_PRODUCT", "FLOW_BREAKDOWN", "UNIT_MEASURE",
                     "OBS_VALUE"} <= set(reader.fieldnames), f"JODI {path}: unexpected columns")
            n = 0
            for r in reader:
                if (r["ENERGY_PRODUCT"] != "CRUDEOIL" or r["FLOW_BREAKDOWN"] != "INDPROD"
                        or r["UNIT_MEASURE"] != "KBD"):
                    continue
                iso3 = COUNTRIES.iso2_to_iso3.get(r["REF_AREA"])
                raw = r["OBS_VALUE"]
                if not iso3 or raw in ("", "-", "x", "NA"):
                    continue
                flags = ["JODI"]
                if r.get("ASSESSMENT_CODE") == "3":
                    flags.append("UNASSESSED")
                res.observations.append(Observation(
                    entity=iso3, metric="crude_prod", value=float(raw), unit="KBD",
                    observation_date=f"{r['TIME_PERIOD']}-01", source="JODI", release_cadence="monthly",
                    period_label=r["TIME_PERIOD"], flags=flags))
                n += 1
            res.notes.append(f"{path.rsplit('/', 1)[-1]}: {n} rows")
            if lm:
                res.kv[key] = lm
        self.mark_backfilled(res)
        return res
