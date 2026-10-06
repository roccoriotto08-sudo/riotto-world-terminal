"""GDELT 2.0 event export  (free, no key) -> geocoded conflict / coercion / sanctions / protest events.

Every 15 minutes GDELT publishes an export zip (61 tab-separated columns). We keep
only material CAMEO codes with enough independent coverage, collapse duplicates
by (country, category, place), and grade severity 1-5 from the CAMEO root,
coverage and Goldstein score. GDELT is machine-coded news: treat single events as
leads, and the UI links the source article for verification.
"""
import csv
import io
import zipfile
from datetime import datetime, timedelta
from typing import Dict, List

from ..geo import COUNTRIES
from ..httpclient import ProviderError, require
from ..schema import AdapterResult, GeoEvent
from .base import Adapter

LAST = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
FILE = "http://data.gdeltproject.org/gdeltv2/{ts}.export.CSV.zip"

# column indexes in the 2.0 export
C_ID, C_A1, C_A2, C_CODE, C_BASE, C_ROOT = 0, 6, 16, 26, 27, 28
C_GOLD, C_MENT, C_SRC, C_ART = 30, 31, 32, 33
C_GTYPE, C_GNAME, C_GCC, C_LAT, C_LON, C_ADDED, C_URL = 51, 52, 53, 56, 57, 59, 60

ROOTS = {"14": ("PROTEST", 1), "15": ("COERCION", 1), "17": ("COERCION", 2),
         "18": ("CONFLICT", 2), "19": ("CONFLICT", 2), "20": ("CONFLICT", 3)}
# Conflict/coercion rows must involve an armed or state actor; this drops the large
# volume of domestic crime that the CAMEO coder files under "use of force".
ARMED = {"MIL", "REB", "INS", "SEP", "UAF", "GOV"}
C_TYPES = (12, 13, 14, 22, 23, 24)
CAMEO = {"14": "Protest", "15": "Military posture", "163": "Sanctions/embargo", "17": "Coercion",
         "18": "Assault", "19": "Armed clash", "20": "Mass violence", "190": "Use of conventional force",
         "193": "Small-arms fighting", "194": "Artillery/tank fighting", "195": "Aerial weapons",
         "180": "Unconventional violence", "183": "Bombing", "186": "Assassination", "172": "Administrative sanctions",
         "175": "Repression", "152": "Military alert", "154": "Mobilization"}
MIN_SOURCES = 3
MIN_ARTICLES = 8


def classify(row: List[str]):
    base, root = row[C_BASE], row[C_ROOT]
    if base == "163":
        return "SANCTIONS", 3
    return ROOTS.get(root, (None, 0))


def severity(base_sev: int, articles: int, goldstein: float) -> int:
    """CAMEO root sets the base; coverage can lift it by at most 2 levels."""
    s = base_sev
    if articles >= 30:
        s += 1
    if articles >= 100 and goldstein <= -9:
        s += 1
    return max(1, min(5, s, base_sev + 2))


def parse_export(blob: bytes) -> List[GeoEvent]:
    zf = zipfile.ZipFile(io.BytesIO(blob))
    name = zf.namelist()[0]
    text = zf.read(name).decode("utf-8", errors="replace")
    groups: Dict[tuple, dict] = {}
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        require(len(row) >= 61, f"GDELT: expected 61 columns, got {len(row)}")
        cat, base_sev = classify(row)
        if not cat or not row[C_LAT] or not row[C_LON]:
            continue
        if cat in ("CONFLICT", "COERCION") and not any(row[i] in ARMED for i in C_TYPES):
            continue
        try:
            n_src, n_art, gold = int(row[C_SRC]), int(row[C_ART]), float(row[C_GOLD] or 0)
        except ValueError:
            continue
        if n_src < MIN_SOURCES and n_art < MIN_ARTICLES:
            continue
        iso3 = COUNTRIES.fips_to_iso3.get(row[C_GCC])
        key = (iso3, cat, row[C_GNAME])
        g = groups.get(key)
        if g is None:
            groups[key] = {"row": row, "best": n_art, "n_art": n_art, "gold": gold, "cat": cat,
                           "base_sev": base_sev, "iso3": iso3, "count": 1}
            continue
        g["count"] += 1
        g["n_art"] += n_art
        g["gold"] = min(g["gold"], gold)
        g["base_sev"] = max(g["base_sev"], base_sev)
        if n_art > g["best"]:          # representative row = best-covered event
            g["row"], g["best"] = row, n_art
    out = []
    for g in groups.values():
        row = g["row"]
        added = datetime.strptime(row[C_ADDED], "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
        what = CAMEO.get(row[C_CODE]) or CAMEO.get(row[C_BASE]) or CAMEO.get(row[C_ROOT], "Event")
        actors = " → ".join(a for a in (row[C_A1].title(), row[C_A2].title()) if a)
        title = f"{what}: {actors + ' — ' if actors else ''}{row[C_GNAME]}"
        if g["count"] > 1:
            title += f" (+{g['count'] - 1} related)"
        out.append(GeoEvent(
            id=f"GDELT:{row[C_ID]}", category=g["cat"], title=title[:220],
            lat=float(row[C_LAT]), lon=float(row[C_LON]), country=g["iso3"],
            severity=severity(g["base_sev"], g["n_art"], g["gold"]), ts=added, source="GDELT",
            url=row[C_URL] or None))
    return out


class GdeltAdapter(Adapter):
    name = "GDELT"
    free = True
    description = "GDELT 2.0: geocoded conflict, coercion, sanctions and protest events (15-min)"
    signup_url = "https://www.gdeltproject.org/data.html (no key)"

    async def poll(self) -> AdapterResult:
        txt = await self.http.get_text(LAST)
        line = next((l for l in txt.splitlines() if l.endswith(".export.CSV.zip")), None)
        require(line is not None, "GDELT lastupdate.txt: no export line")
        ts = line.split()[-1].rsplit("/", 1)[-1].split(".")[0]
        last_done = self.db.kv_get("GDELT:last_ts")
        if last_done == ts:
            return AdapterResult(notes=["no new GDELT file"])
        latest = datetime.strptime(ts, "%Y%m%d%H%M%S")
        n_back = int(self.cfg.get("backfill_files", 8)) if not last_done else 0
        if last_done:  # catch up on missed files (max 8) after downtime
            gap = int((latest - datetime.strptime(last_done, "%Y%m%d%H%M%S")).total_seconds() // 900) - 1
            n_back = max(0, min(gap, 8))
        stamps = [(latest - timedelta(minutes=15 * k)).strftime("%Y%m%d%H%M%S") for k in range(n_back, -1, -1)]
        res = AdapterResult()
        for st in stamps:
            try:
                blob = await self.http.get_bytes(FILE.format(ts=st))
                res.events.extend(parse_export(blob))
            except (ProviderError, zipfile.BadZipFile) as exc:
                res.partial_errors += 1
                res.notes.append(f"{st}: {exc}")
        res.kv["GDELT:last_ts"] = ts
        return res
