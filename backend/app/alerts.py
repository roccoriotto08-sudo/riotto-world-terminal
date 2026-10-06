"""Config-driven rules engine (config/rules.yaml) + read-through annotation for events.

Alerts fire only from observations that were NEW in this ingest and within the
rule's `fresh_days`. Each alert stores `trigger`: the exact rows used (entity,
metric, source, observation_date, value) and the computed statistic.
"""
import math
from datetime import date, timedelta
from typing import List, Optional

from .db import DB
from .geo import COUNTRIES
from .schema import GeoEvent, Observation, utcnow_iso
from .settings import SETTINGS

_CHOKE = {c["id"]: c for c in SETTINGS.chokepoints}
_KPI_LABEL = {k["entity"]: k["label"] for k in SETTINGS.kpis["kpis"] + SETTINGS.kpis.get("commodities", [])}
_METRIC_TAGS = {"crude_prod": ["OIL"], "crude_stocks_comm": ["OIL"], "policy_rate": ["RATES"],
                "spread": ["RATES"], "transits_total": []}


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def display_name(entity: str) -> str:
    if entity in _CHOKE:
        return _CHOKE[entity]["name"]
    if entity in _KPI_LABEL:
        return _KPI_LABEL[entity]
    return COUNTRIES.name(entity)


def location(entity: str):
    if entity in _CHOKE:
        return _CHOKE[entity]["lat"], _CHOKE[entity]["lon"]
    if entity in COUNTRIES.by_iso3:
        return COUNTRIES.centroid(entity)
    return None, None


def _pt(row) -> dict:
    return {"entity": row["entity"], "metric": row["metric"], "source": row["source"],
            "observation_date": row["observation_date"], "value": row["value"], "unit": row["unit"]}


def _opt(o: Observation) -> dict:
    return {"entity": o.entity, "metric": o.metric, "source": o.source,
            "observation_date": o.observation_date, "value": o.value, "unit": o.unit}


class _Fmt(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fill(template: str, ctx: dict) -> str:
    try:
        return template.format_map(_Fmt(ctx))
    except (ValueError, KeyError, IndexError):
        return template


class AlertEngine:
    def __init__(self, db: DB):
        self.db = db
        self.rules = SETTINGS.rules.get("rules", [])

    def _matches(self, rule: dict, o: Observation) -> bool:
        if rule["metric"] != o.metric:
            return False
        ents = rule.get("entities", "*")
        return ents == "*" or o.entity in ents

    def evaluate(self, new: List[Observation]) -> List[GeoEvent]:
        newest = {}
        for o in new:
            k = (o.entity, o.metric, o.source)
            if k not in newest or o.observation_date > newest[k].observation_date:
                newest[k] = o
        out = []
        today = date.today()
        for o in newest.values():
            for rule in self.rules:
                if not self._matches(rule, o):
                    continue
                if (today - _d(o.observation_date)).days > rule.get("fresh_days", 30):
                    continue   # backfilled history: never alert on it
                handler = getattr(self, "_" + rule["type"], None)
                if handler is None:
                    continue
                hit = handler(rule, o)
                if hit:
                    out.append(self._make(rule, o, *hit))
        return out

    # ---- rule types: return (ctx, trigger_points) or None --------------------
    def _ref(self, o: Observation, lookback: int, tol: int):
        target = _d(o.observation_date) - timedelta(days=lookback)
        ref = self.db.on_or_before(o.entity, o.metric, o.source, target.isoformat())
        if not ref or (target - _d(ref["observation_date"])).days > tol:
            return None
        if ref["observation_date"][:10] >= o.observation_date[:10]:
            return None
        return ref

    @staticmethod
    def _cmp(trigger: dict, x: float) -> bool:
        if "lt" in trigger and not x < trigger["lt"]:
            return False
        if "gt" in trigger and not x > trigger["gt"]:
            return False
        if "abs_gt" in trigger and not abs(x) > trigger["abs_gt"]:
            return False
        if "abs_gte" in trigger and not abs(x) >= trigger["abs_gte"]:
            return False
        return True

    def _pct_change(self, rule, o):
        if o.value < rule.get("min_value", -math.inf):
            return None
        ref = self._ref(o, rule["lookback_days"], rule.get("tolerance_days", 5))
        if not ref or ref["value"] == 0:
            return None
        pct = (o.value / ref["value"] - 1) * 100
        if not self._cmp(rule["trigger"], pct):
            return None
        up = pct > 0
        return ({"pct": pct, "value": o.value, "ref": ref["value"],
                 "dir_same": "up" if up else "down", "dir_opp": "down" if up else "up"},
                {"current": _opt(o), "reference": _pt(ref), "stat": {"pct_change": round(pct, 3)}})

    def _abs_change(self, rule, o):
        prev = self.db.previous(o.entity, o.metric, o.source, o.observation_date)
        if not prev:
            return None
        delta = o.value - prev["value"]
        if not self._cmp(rule["trigger"], delta):
            return None
        hike = delta > 0
        return ({"delta": delta, "value": o.value, "dir_up_if_hike": "up" if hike else "down"},
                {"current": _opt(o), "reference": _pt(prev), "stat": {"abs_change": round(delta, 4)}})

    def _baseline_ratio(self, rule, o):
        end = _d(o.observation_date)
        rdays, bdays = rule["recent_days"], rule["baseline_days"]
        rows = self.db.series(o.entity, o.metric, o.source,
                              since=(end - timedelta(days=rdays + bdays - 1)).isoformat(), until=end.isoformat())
        cut = (end - timedelta(days=rdays - 1)).isoformat()
        recent = [r["value"] for r in rows if r["observation_date"] >= cut]
        base = [r["value"] for r in rows if r["observation_date"] < cut]
        if len(recent) < rdays * 0.7 or len(base) < bdays * 0.7:
            return None
        ra, ba = sum(recent) / len(recent), sum(base) / len(base)
        if ba <= 0:
            return None
        pct = (ra / ba - 1) * 100
        if not self._cmp(rule["trigger"], pct):
            return None
        return ({"pct": pct, "recent": ra, "baseline": ba, "value": o.value},
                {"current": _opt(o), "stat": {"recent_avg": round(ra, 3), "baseline_avg": round(ba, 3),
                                              "pct_vs_baseline": round(pct, 2), "recent_n": len(recent),
                                              "baseline_n": len(base), "window_end": end.isoformat()}})

    def _level_and_change(self, rule, o):
        if not o.value < rule["level"]:
            return None
        ref = self._ref(o, rule["lookback_days"], 4)
        if not ref:
            return None
        ch = o.value - ref["value"]
        if not ch < rule["change_lt"]:
            return None
        return ({"value_bp": o.value * 100, "change_bp": ch * 100, "value": o.value},
                {"current": _opt(o), "reference": _pt(ref), "stat": {"change": round(ch, 4)}})

    # -------------------------------------------------------------------------
    def _make(self, rule: dict, o: Observation, ctx: dict, trigger: dict) -> GeoEvent:
        name = display_name(o.entity)
        ctx = dict(ctx, name=name, period=o.period_label or o.observation_date[:10])
        rt = rule.get("readthrough", {})
        assets = [{"asset": _fill(a["asset"], ctx), "direction": _fill(str(a["direction"]), ctx), "p": a["p"]}
                  for a in rt.get("assets", [])]
        lat, lon = location(o.entity)
        tags = list(_METRIC_TAGS.get(o.metric, []))
        if o.entity in _CHOKE:
            tags += _CHOKE[o.entity].get("commodities", [])
        if o.entity in ("BRENT", "WTI"):
            tags.append("OIL")
        trigger["rule"] = {k: rule[k] for k in ("id", "type", "metric", "trigger") if k in rule}
        return GeoEvent(
            id=f"ALERT:{rule['id']}:{o.entity}:{o.observation_date[:10]}", kind="ALERT", category="SIGNAL",
            title=f"{rule['name']}: {_fill(rt.get('text', name), ctx)}"[:220], lat=lat, lon=lon,
            country=o.entity if o.entity in COUNTRIES.by_iso3 else None, severity=rule["severity"],
            # ts = detection time: the alert is news when the data is published, not on the
            # observation date (PortWatch/JODI lag days-months). The data date lives in `trigger`.
            ts=utcnow_iso(),
            source=f"RULES/{o.source}", commodities=sorted(set(tags)),
            readthrough={"text": _fill(rt.get("text", ""), ctx), "assets": assets, "basis": "rule prior (config)"},
            rule_id=rule["id"], trigger=trigger, entity=o.entity)


# ---------------------------------------------------------------- events
def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def nearest_chokepoint(lat: Optional[float], lon: Optional[float], radius_km: float):
    if lat is None or lon is None:
        return None
    best = min(((_km(lat, lon, c["lat"], c["lon"]), c) for c in SETTINGS.chokepoints), key=lambda t: t[0])
    return best[1] if best[0] <= radius_km else None


def annotate_event(e: GeoEvent) -> GeoEvent:
    """Attach commodity tags + read-through from config/readthrough.yaml."""
    cfg = SETTINGS.readthrough
    for m in cfg.get("maps", []):
        if e.country in m["countries"] and e.category in m["categories"] and e.severity >= m.get("min_severity", 1):
            return e.model_copy(update={
                "commodities": sorted(set(e.commodities) | set(m.get("commodities", []))),
                "readthrough": {"text": m["name"], "assets": m["assets"], "basis": "event map prior (config)"}})
    prox = cfg.get("chokepoint_proximity")
    if prox and e.category in prox["categories"] and e.severity >= prox.get("min_severity", 1):
        c = nearest_chokepoint(e.lat, e.lon, prox["radius_km"])
        if c:
            return e.model_copy(update={
                "commodities": sorted(set(e.commodities) | set(c.get("commodities", []))),
                "entity": c["id"],
                "readthrough": {"text": f"Near {c['name']}", "assets": prox["assets"],
                                "basis": "chokepoint proximity prior (config)"}})
    return e
