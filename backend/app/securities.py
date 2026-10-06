"""Security catalog for the Bloomberg-style command line.

Hand-written aliases come from config/securities.yaml; curve tenors, ECO series, FX,
10Y government yields and watchlist equities are generated here. Each entry points at
a stored (entity, metric) series - the catalog never implies data we do not hold.
"""
import re
from typing import Dict, List, Optional

from .geo import COUNTRIES
from .settings import SETTINGS

YELLOW_KEYS = ["GOVT", "CORP", "MTGE", "M-MKT", "MUNI", "PFD", "EQUITY", "COMDTY", "INDEX", "CRNCY"]
FUNCTIONS = {"GP", "DES", "GC", "ECO", "HELP", "MENU", "CN"}

CURVE = [("1M", "UST1M", 1 / 12), ("3M", "UST3M", 0.25), ("6M", "UST6M", 0.5), ("1Y", "UST1Y", 1),
         ("2Y", "UST2Y", 2), ("3Y", "UST3Y", 3), ("5Y", "UST5Y", 5), ("7Y", "UST7Y", 7),
         ("10Y", "UST10Y", 10), ("20Y", "UST20Y", 20), ("30Y", "UST30Y", 30)]
# Bloomberg generic Treasury tickers
_USGG = {"1M": "USGG1M", "3M": "USGG3M", "6M": "USGG6M", "1Y": "USGG12M", "2Y": "USGG2YR", "3Y": "USGG3YR",
         "5Y": "USGG5YR", "7Y": "USGG7YR", "10Y": "USGG10YR", "20Y": "USGG20Y", "30Y": "USGG30YR"}
# currency code -> FX entity (stored as local per USD)
_CCY = {"EUR": "EA", "GBP": "GBR", "AUD": "AUS", "JPY": "JPN", "CNY": "CHN", "CAD": "CAN", "KRW": "KOR",
        "INR": "IND", "BRL": "BRA", "MXN": "MEX", "CHF": "CHE", "NOK": "NOR", "SEK": "SWE", "ZAR": "ZAF",
        "THB": "THA", "SGD": "SGP"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().upper())


def build_catalog() -> List[dict]:
    cat: List[dict] = []
    for s in SETTINGS.securities:
        cat.append(dict(s, kind="kpi"))
    for tenor, ent, years in CURVE:
        cat.append({"ticker": _USGG[tenor], "yk": "GOVT", "name": f"US Treasury {tenor} (constant maturity)",
                    "entity": ent, "metric": "yield", "aliases": [f"UST{tenor}", f"T{tenor}", f"GT{tenor}"],
                    "kind": "curve", "tenor": tenor, "years": years})
    seen = set()
    for rel in SETTINGS.eco.get("releases", []):
        for ind in rel["indicators"]:
            if ind["series"] in seen:
                continue
            seen.add(ind["series"])
            cat.append({"ticker": ind["series"], "yk": "INDEX", "name": f"{ind['label']} — {rel['name']}",
                        "entity": "US_ECO", "metric": ind["series"], "aliases": [], "kind": "eco",
                        "transform": ind["transform"]})
    for ccy, ent in _CCY.items():
        # always quoted local-per-USD (what we store). For EUR/GBP/AUD that is the INVERSE of the
        # market convention, so they deliberately get no EURUSD/GBPUSD/AUDUSD alias.
        inv = ccy in ("EUR", "GBP", "AUD")
        cat.append({"ticker": f"USD{ccy}", "yk": "CRNCY",
                    "name": f"{ccy} per USD (FRED H.10){' — inverse of ' + ccy + 'USD' if inv else ''}",
                    "entity": ent, "metric": "fx_per_usd", "aliases": [ccy] if ccy != "JPY" else [],
                    "kind": "fx", "note": "stored as local currency per USD"})
    for s in SETTINGS.fred.get("series", []):
        if s["metric"] == "yield_10y":
            iso = s["entity"]
            cat.append({"ticker": f"{iso}10Y", "yk": "GOVT", "name": f"{COUNTRIES.name(iso)} 10Y govt yield (OECD, monthly avg)",
                        "entity": iso, "metric": "yield_10y", "aliases": [f"{iso} 10Y"], "kind": "yield10"})
    for h in SETTINGS.watchlist.get("holdings", []):
        cat.append({"ticker": f"{h['ticker']} US", "yk": "EQUITY", "name": h.get("name", h["ticker"]),
                    "entity": f"EQ:{h['ticker']}", "metric": "price", "aliases": [h["ticker"]], "kind": "equity"})
    return cat


CATALOG = build_catalog()
_INDEX: Dict[str, dict] = {}
for _s in CATALOG:
    for _k in [_s["ticker"]] + list(_s.get("aliases", [])):
        _INDEX.setdefault(_norm(_k), _s)


def parse(command: str) -> Optional[dict]:
    """'CL1 COMDTY GP' -> {security, yk, function}. Returns None if no security matches.
    The yellow key is optional (it disambiguates only); the function defaults to None
    (= show the security's numbered menu, like <GO> on a bare ticker)."""
    toks = _norm(command).split(" ")
    func = None
    if toks and toks[-1] in FUNCTIONS:
        func = toks.pop()
    yk = None
    if toks and toks[-1] in YELLOW_KEYS:
        yk = toks.pop()
    key = " ".join(toks)
    sec = _INDEX.get(key)
    if sec is None and toks and len(toks) > 1:
        sec = _INDEX.get(toks[0])          # "XOM US" -> XOM
    if sec is None:
        return None
    if yk and sec["yk"] != yk:
        alt = next((s for s in CATALOG if s["yk"] == yk and (key == _norm(s["ticker"]) or
                                                            key in map(_norm, s.get("aliases", [])))), None)
        if alt is None:
            return {"error": f"{key} is not a {yk} security (it is {sec['yk']})"}
        sec = alt
    return {"security": sec, "yk": yk or sec["yk"], "function": func}
