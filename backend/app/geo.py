"""Country reference built from the vendored Natural Earth 110m file (public domain)."""
import json
from typing import Dict, List, Optional

from .settings import ROOT

GEOJSON_PATH = ROOT / "frontend" / "data" / "countries.geojson"

# Euro area membership (Bulgaria joined 2026-01-01). Used to expand ECB / EUR rows.
EA_MEMBERS = ["AUT", "BEL", "BGR", "HRV", "CYP", "EST", "FIN", "FRA", "DEU", "GRC", "IRL", "ITA",
              "LVA", "LTU", "LUX", "MLT", "NLD", "PRT", "SVK", "SVN", "ESP"]

# FIPS 10-4 codes that Natural Earth leaves blank but GDELT uses (Gaza/West Bank both -> PSE).
_FIPS_EXTRA = {"NO": "NOR", "FR": "FRA", "IS": "ISR", "GZ": "PSE", "WE": "PSE", "OD": "SSD"}


class Countries:
    def __init__(self) -> None:
        fc = json.loads(GEOJSON_PATH.read_text())
        self.by_iso3: Dict[str, dict] = {}
        self.iso2_to_iso3: Dict[str, str] = {}
        self.fips_to_iso3: Dict[str, str] = {}
        self.n3_to_iso3: Dict[str, str] = {}
        self.name_to_iso3: Dict[str, str] = {}
        for f in fc["features"]:
            p = f["properties"]
            iso3 = p["iso3"]
            self.by_iso3[iso3] = p
            if p.get("iso2"):
                self.iso2_to_iso3[p["iso2"]] = iso3
            if p.get("fips"):
                self.fips_to_iso3[p["fips"]] = iso3
            if p.get("isoN3") and p["isoN3"] != "-99":
                self.n3_to_iso3[str(int(p["isoN3"]))] = iso3
            self.name_to_iso3[p["name"].upper()] = iso3
        self.fips_to_iso3.update(_FIPS_EXTRA)

    def name(self, iso3: Optional[str]) -> str:
        if iso3 == "EA":
            return "Euro area"
        p = self.by_iso3.get(iso3 or "")
        return p["name"] if p else (iso3 or "?")

    def region(self, iso3: Optional[str]) -> Optional[str]:
        p = self.by_iso3.get(iso3 or "")
        return p["subregion"] if p else None

    def centroid(self, iso3: str):
        p = self.by_iso3.get(iso3)
        return (p["lat"], p["lon"]) if p else (None, None)

    def expand(self, entity: str) -> List[str]:
        """Entity -> list of ISO3 countries it should paint on the map."""
        if entity == "EA":
            return EA_MEMBERS
        return [entity] if entity in self.by_iso3 else []

    def resolve(self, text: str) -> Optional[str]:
        t = text.strip().upper()
        if t in self.by_iso3:
            return t
        if t in self.iso2_to_iso3:
            return self.iso2_to_iso3[t]
        if t in self.name_to_iso3:
            return self.name_to_iso3[t]
        for name, iso3 in self.name_to_iso3.items():
            if name.startswith(t) and len(t) >= 4:
                return iso3
        return None


COUNTRIES = Countries()
