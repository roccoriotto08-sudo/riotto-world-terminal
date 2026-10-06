"""Read models for the API. Nothing here creates values: every number is a stored
observation or an explicitly DERIVED calculation over stored observations."""
import json
from datetime import date, timedelta
from typing import Dict, List, Optional

from .alerts import display_name
from .db import DB
from .geo import COUNTRIES, EA_MEMBERS
from .settings import SETTINGS
from .status import StatusResolver
from .vessels import store_for


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _row(r) -> dict:
    d = dict(r)
    d.pop("_d", None)
    d["flags"] = json.loads(d.get("flags") or "[]")
    return d


def _daily(rows: list) -> list:
    """Collapse intraday rows to the last print per day (for sparklines / changes)."""
    out: Dict[str, dict] = {}
    for r in rows:
        out[r["observation_date"][:10]] = r
    return [out[k] for k in sorted(out)]


MAX_AGE_DAYS = {"annual": 3 * 366, "quarterly": 400, "monthly": 550, "weekly": 120, "daily": 120, "intraday": 30}


class Analytics:
    def __init__(self, db: DB, status: StatusResolver):
        self.db = db
        self.st = status

    # ------------------------------------------------------------- helpers
    def _stamp(self, d: dict) -> dict:
        status, age = self.st.status(d.get("source"), d.get("release_cadence"))
        d["status"] = status
        d["provider_age_s"] = age
        return d

    def _changes(self, entity, metric, source, latest, kind) -> dict:
        def ref_for(days, tol):
            if days == 1:
                rows = self.db._q("SELECT * FROM observations WHERE entity=? AND metric=? AND source=? "
                                  "AND observation_date<? ORDER BY observation_date DESC LIMIT 1",
                                  (entity, metric, source, latest["observation_date"][:10]))
                r = rows[0] if rows else None
                return r if r and (_d(latest["observation_date"]) - _d(r["observation_date"])).days <= tol else None
            target = _d(latest["observation_date"]) - timedelta(days=days)
            r = self.db.on_or_before(entity, metric, source, target.isoformat())
            return r if r and (target - _d(r["observation_date"])).days <= tol else None

        out = {}
        for key, days, tol in (("d1", 1, 5), ("w1", 7, 4), ("m1", 30, 10)):
            r = ref_for(days, tol)
            if not r:
                out[key] = None
                continue
            if kind == "bp":
                out[key] = round((latest["value"] - r["value"]) * 100, 1)
            else:
                out[key] = round((latest["value"] / r["value"] - 1) * 100, 2) if r["value"] else None
        return out

    # ----------------------------------------------------------------- KPIs
    def kpi(self, k: dict) -> dict:
        sources = list(k.get("sources", []))
        if SETTINGS.demo_mode:
            sources.append("DEMO")
        best = None
        for i, s in enumerate(sources):
            r = self.db.latest(k["entity"], k["metric"], s)
            if r and (best is None or r["observation_date"][:19] > best[1]["observation_date"][:19]):
                best = (s, r)
        base = {"id": k["id"], "label": k["label"], "unit": k["unit"], "change_kind": k.get("change", "pct"),
                "entity": k["entity"], "metric": k["metric"], "hub": k.get("hub")}
        if not best:
            return dict(base, value=None, status="ERROR", source=None, observation_date=None,
                        changes={"d1": None, "w1": None, "m1": None}, spark=[], flags=[],
                        missing=self._why_missing(sources))
        src, r = best
        d = self._stamp(dict(base, **_row(r)))
        d["unit"] = k["unit"]
        changes = self._changes(k["entity"], k["metric"], src, r, k.get("change", "pct"))
        d["change_source"] = src
        if all(v is None for v in changes.values()):
            # market feed has no history yet: fall back to the next source and SAY so
            for s in sources:
                if s == src:
                    continue
                alt = self.db.latest(k["entity"], k["metric"], s)
                if alt:
                    changes = self._changes(k["entity"], k["metric"], s, alt, k.get("change", "pct"))
                    d["change_source"] = s
                    break
        d["changes"] = changes
        since = (_d(r["observation_date"]) - timedelta(days=400)).isoformat()
        hist_src = d["change_source"]
        hist = _daily(self.db.series(k["entity"], k["metric"], hist_src, since=since))
        d["spark"] = [[h["observation_date"][:10], round(h["value"], 4)] for h in hist[-90:]]
        d["spark_source"] = hist_src
        return d

    def _why_missing(self, sources: List[str]) -> str:
        states = self.st.states_fn()
        reasons = []
        # most actionable first: free feeds before the paid market provider
        for s in sorted(sources, key=lambda x: x == "MARKET"):
            st = states.get(s, {})
            if not st.get("enabled"):
                reasons.append(st.get("reason") or f"{s} disabled")
            elif st.get("last_error"):
                reasons.append(f"{s}: {st['last_error'][:60]}")
            else:
                reasons.append(f"{s}: no data yet")
        return "; ".join(reasons)

    def shown(self, k: dict) -> bool:
        """Public site: hide licence-restricted KPIs and ones no enabled feed can ever fill
        (e.g. gold without a redistributable market feed) instead of a permanent ERROR cell."""
        if SETTINGS.hidden(k["entity"]):
            return False
        if not SETTINGS.public_mode:
            return True
        return any(s in self.st.adapters and self.st.adapters[s].enabled()[0] for s in k.get("sources", []))

    def kpis(self) -> List[dict]:
        return [self.kpi(k) for k in SETTINGS.kpis["kpis"] if self.shown(k)]

    def commodities(self) -> List[dict]:
        return [self.kpi(k) for k in SETTINGS.kpis.get("commodities", []) if self.shown(k)]

    def kpi_history(self, kid: str, days: int) -> dict:
        k = next((x for x in SETTINGS.kpis["kpis"] + SETTINGS.kpis.get("commodities", []) if x["id"] == kid), None)
        if not k or SETTINGS.hidden(k["entity"]):
            return {}
        snap = self.kpi(k)
        src = snap.get("change_source") or snap.get("source")
        rows = []
        if src:
            since = (date.today() - timedelta(days=days)).isoformat()
            rows = _daily(self.db.series(k["entity"], k["metric"], src, since=since))
        return dict(snap, history=[[r["observation_date"][:10], r["value"]] for r in rows], history_source=src)

    # ----------------------------------------------------------- choropleth
    def choropleth(self, metric: str, as_of: Optional[str] = None) -> dict:
        cfg = SETTINGS.metrics["choropleth"][metric]
        ref_date = _d(as_of) if as_of else date.today()
        values: Dict[str, dict] = {}
        if metric == "crude_prod_yoy":
            for r in self.db.latest_per_entity("crude_prod", cfg["source"], as_of):
                cur = _d(r["observation_date"])
                ya = self.db.latest(r["entity"], "crude_prod", cfg["source"],
                                    as_of=cur.replace(year=cur.year - 1).isoformat())
                if not ya or ya["observation_date"] != cur.replace(year=cur.year - 1).isoformat() or ya["value"] < 10:
                    continue   # needs the exact year-ago month and a producer of >= 10 kb/d
                values[r["entity"]] = self._cell(r, (r["value"] / ya["value"] - 1) * 100, ["DERIVED"],
                                                 cfg, ref_date, extra={"level": r["value"], "level_unit": "kb/d",
                                                                       "ref_value": ya["value"]})
        elif metric == "fx_1m":
            for r in self.db.latest_per_entity("fx_per_usd", cfg["source"], as_of):
                target = _d(r["observation_date"]) - timedelta(days=30)
                ref = self.db.on_or_before(r["entity"], "fx_per_usd", r["source"], target.isoformat())
                if not ref or (target - _d(ref["observation_date"])).days > 7:
                    continue
                pct = (ref["value"] / r["value"] - 1) * 100     # + = local currency stronger vs USD
                values[r["entity"]] = self._cell(r, pct, ["DERIVED"], cfg, ref_date,
                                                 extra={"level": r["value"], "level_unit": "local per USD"})
        else:
            for r in self.db.latest_per_entity(metric, cfg["source"], as_of):
                values[r["entity"]] = self._cell(r, r["value"], [], cfg, ref_date)
        # expand euro-area rows onto members lacking their own value
        if "EA" in values:
            ea = values.pop("EA")
            for m in EA_MEMBERS:
                values.setdefault(m, dict(ea, flags=ea["flags"] + ["EURO_AREA"]))
        vals = [v["value"] for v in values.values()]
        return {"metric": metric, "label": cfg["label"], "unit": cfg["unit"], "scale": cfg["scale"],
                "domain": cfg["domain"], "mid": cfg.get("mid"), "source": cfg["source"], "as_of": as_of,
                "provider_status": self.st.status(cfg["source"], cfg["cadence"])[0],
                "count": len(values), "values": values,
                "range": [min(vals), max(vals)] if vals else None}

    def _cell(self, r, value, flags, cfg, ref_date, extra=None) -> dict:
        rf = json.loads(r["flags"] or "[]")
        old = (ref_date - _d(r["observation_date"])).days > MAX_AGE_DAYS.get(r["release_cadence"], 400)
        d = {"value": round(value, 4), "observation_date": r["observation_date"], "period": r["period_label"],
             "source": r["source"], "flags": rf + flags + (["OLD"] if old else [])}
        if extra:
            d.update(extra)
        return d

    # ------------------------------------------------------------ production
    def production(self, as_of: Optional[str] = None) -> List[dict]:
        thr = SETTINGS.main["alerts"]["production_drop_mom_pct"]
        out = []
        ents = {r["entity"] for r in self.db.latest_per_entity("crude_prod", None, as_of)}
        for e in ents:
            prim = None
            for src in ("JODI", "EIA"):
                r = self.db.latest(e, "crude_prod", src, as_of)
                if r and (prim is None or r["observation_date"] > prim["observation_date"]):
                    prim = r
            if not prim or prim["value"] < 50:
                continue
            cur = _d(prim["observation_date"])
            prev_m = (cur.replace(day=1) - timedelta(days=1)).replace(day=1)
            pm = self.db.latest(e, "crude_prod", prim["source"], prev_m.isoformat())
            ya = self.db.latest(e, "crude_prod", prim["source"], cur.replace(year=cur.year - 1).isoformat())
            mom = (prim["value"] / pm["value"] - 1) * 100 if pm and pm["observation_date"] == prev_m.isoformat() and pm["value"] else None
            yoy = (prim["value"] / ya["value"] - 1) * 100 if ya and ya["observation_date"] == cur.replace(year=cur.year - 1).isoformat() and ya["value"] else None
            other = self.db.latest(e, "crude_prod", "EIA" if prim["source"] == "JODI" else "JODI", prim["observation_date"])
            xc = None
            if other and other["observation_date"] == prim["observation_date"]:
                mean = (other["value"] + prim["value"]) / 2
                xc = {"source": other["source"], "value": other["value"],
                      "diff_pct": round((prim["value"] - other["value"]) / mean * 100, 2) if mean else None,
                      "flag": bool(mean) and abs((prim["value"] - other["value"]) / mean * 100) >= SETTINGS.main["crosscheck"]["production_pct_threshold"]}
            q = self.db.latest(e, "opec_quota", "OPEC_CFG", prim["observation_date"])
            quota = None
            if q and q["observation_date"] == prim["observation_date"]:
                quota = {"value": q["value"], "vs_actual_kbd": round(prim["value"] - q["value"], 1),
                         "source": "manual cfg", "flags": json.loads(q["flags"])}
            lat, lon = COUNTRIES.centroid(e)
            ref = _d(as_of) if as_of else date.today()
            months_old = (ref.year - cur.year) * 12 + ref.month - cur.month
            stale_period = months_old > 6      # reporter stopped / lagging: never drives the drop marker
            out.append(self._stamp({
                "entity": e, "name": COUNTRIES.name(e), "lat": lat, "lon": lon, "value": prim["value"], "unit": "kb/d",
                "period": prim["period_label"], "observation_date": prim["observation_date"],
                "source": prim["source"], "release_cadence": "monthly", "flags": json.loads(prim["flags"]),
                "mom_pct": round(mom, 2) if mom is not None else None,
                "yoy_pct": round(yoy, 2) if yoy is not None else None,
                "drop_alert": mom is not None and mom <= thr and not stale_period, "threshold_pct": thr,
                "months_old": months_old, "old_period": stale_period,
                "crosscheck": xc, "quota": quota}))
        return sorted(out, key=lambda x: -x["value"])

    # ----------------------------------------------------------- chokepoints
    def chokepoints(self, as_of: Optional[str] = None) -> List[dict]:
        c = SETTINGS.main["chokepoints"]
        rd, bd = c["recent_window_days"], c["baseline_window_days"]
        out = []
        for cp in SETTINGS.chokepoints:
            last = self.db.latest(cp["id"], "transits_total", "PORTWATCH", as_of)
            d = {"id": cp["id"], "name": cp["name"], "lat": cp["lat"], "lon": cp["lon"],
                 "commodities": cp.get("commodities", []), "source": "PORTWATCH", "release_cadence": "daily"}
            if not last:
                out.append(self._stamp(dict(d, state="NO DATA", observation_date=None)))
                continue
            end = _d(last["observation_date"])
            rows = self.db.series(cp["id"], "transits_total", "PORTWATCH",
                                  since=(end - timedelta(days=rd + bd - 1)).isoformat(), until=end.isoformat())
            cut = (end - timedelta(days=rd - 1)).isoformat()
            recent = [r["value"] for r in rows if r["observation_date"] >= cut]
            base = [r["value"] for r in rows if r["observation_date"] < cut]
            tank = self.db.series(cp["id"], "transits_tanker", "PORTWATCH", since=cut, until=end.isoformat())
            ra = sum(recent) / len(recent) if recent else None
            ba = sum(base) / len(base) if base else None
            pct = (ra / ba - 1) * 100 if ra is not None and ba else None
            state = "NORMAL"
            if pct is None:
                state = "NO BASELINE"
            elif pct <= c["disrupted_pct"]:
                state = "DISRUPTED"
            elif pct <= c["elevated_pct"]:
                state = "ELEVATED"
            spark = self.db.series(cp["id"], "transits_total", "PORTWATCH",
                                   since=(end - timedelta(days=120)).isoformat(), until=end.isoformat())
            ais = [] if as_of else store_for(self.db).near(cp["lat"], cp["lon"], 150)
            out.append(self._stamp(dict(
                d, state=state, observation_date=last["observation_date"], last_day=last["value"],
                ais_tankers=len([v for v in ais if not v["stale"]]) if not as_of else None,
                ais_moving=len([v for v in ais if not v["stale"] and v["moving"]]) if not as_of else None,
                recent_avg=round(ra, 2) if ra is not None else None, baseline_avg=round(ba, 2) if ba else None,
                pct_vs_baseline=round(pct, 1) if pct is not None else None,
                tanker_recent_avg=round(sum(t["value"] for t in tank) / len(tank), 2) if tank else None,
                window=f"{rd}d vs prior {bd}d", spark=[[s["observation_date"], s["value"]] for s in spark])))
        return out

    # --------------------------------------------------------------- macro
    def macro_markers(self, as_of: Optional[str] = None) -> List[dict]:
        out = []
        cal = {b["entity"]: b for b in SETTINGS.cb_calendar.get("banks", [])}
        today = (_d(as_of) if as_of else date.today()).isoformat()
        for r in self.db.latest_per_entity("policy_rate", "BIS", as_of):
            e = r["entity"]
            prev = self.db._q("SELECT value, observation_date FROM observations WHERE entity=? AND metric='policy_rate' "
                              "AND source='BIS' AND value<>? AND observation_date<? ORDER BY observation_date DESC LIMIT 1",
                              (e, r["value"], r["observation_date"]))
            lat, lon = COUNTRIES.centroid("DEU" if e == "EA" else e)
            if e == "EA":
                lat, lon = 50.11, 8.68   # ECB, Frankfurt
            b = cal.get(e, {})
            nxt = next((m for m in b.get("meetings", []) if m >= today), None)
            out.append(self._stamp({
                "entity": e, "name": display_name(e) if e != "EA" else "Euro area (ECB)", "lat": lat, "lon": lon,
                "value": r["value"], "observation_date": r["observation_date"], "source": "BIS",
                "release_cadence": "daily", "last_change": dict(prev[0]) if prev else None,
                "next_meeting": nxt, "calendar_verified": b.get("verified") if b else None,
                "calendar_source": b.get("source_url")}))
        return out

    # -------------------------------------------------------------- country
    def country(self, iso3: str, as_of: Optional[str] = None) -> dict:
        ea = iso3 in EA_MEMBERS
        metrics = []
        specs = [("gdp_growth", "WORLDBANK", "GDP growth y/y", "%"), ("cpi", "WORLDBANK", "CPI y/y", "%"),
                 ("current_account", "WORLDBANK", "Current acct", "% GDP"), ("policy_rate", "BIS", "Policy rate", "%"),
                 ("yield_10y", "FRED", "10Y yield", "%"), ("fx_per_usd", "FRED", "FX per USD", "local per USD"),
                 ("crude_prod", "JODI", "Crude output", "kb/d"), ("crude_prod", "EIA", "Crude output (EIA)", "kb/d"),
                 ("crude_prod_steo", "EIA_STEO", "Crude (STEO est.)", "kb/d"), ("opec_quota", "OPEC_CFG", "OPEC+ quota (cfg)", "kb/d")]
        for metric, src, label, unit in specs:
            ent = iso3
            r = self.db.latest(ent, metric, src, as_of)
            if not r and ea and metric in ("policy_rate", "fx_per_usd"):
                ent = "EA"
                r = self.db.latest(ent, metric, src, as_of)
            if not r:
                continue
            hist = self.db.series(ent, metric, src, until=as_of, limit=400)
            hist = _daily(hist)
            n = {"annual": 12, "monthly": 36, "weekly": 52, "daily": 180}.get(r["release_cadence"], 60)
            d = self._stamp(_row(r))
            d.update(label=label, unit=unit, via=("EA" if ent == "EA" else None),
                     spark=[[h["observation_date"][:10], h["value"]] for h in hist[-n:]])
            metrics.append(d)
        # derived: 10Y minus policy rate (dates shown so the mismatch is visible)
        y = next((m for m in metrics if m["metric"] == "yield_10y"), None)
        p = next((m for m in metrics if m["metric"] == "policy_rate"), None)
        derived = []
        if y and p:
            derived.append({"label": "10Y − policy", "value": round(y["value"] - p["value"], 3), "unit": "pp",
                            "flags": ["DERIVED"], "basis": f"10Y {y['observation_date'][:10]} ({y['source']}) − "
                                                           f"policy {p['observation_date'][:10]} (BIS)"})
        cal = next((b for b in SETTINGS.cb_calendar.get("banks", []) if b["entity"] == (("EA" if ea else iso3))), None)
        since = ((_d(as_of) if as_of else date.today()) - timedelta(days=30)).isoformat()
        events = self.db.events(since=since, until=as_of, country=iso3, limit=40)
        events.sort(key=lambda e: e["ts"], reverse=True)
        events.sort(key=lambda e: -e["severity"])
        disc = [d for d in self.db.discrepancies(500) if d["entity"] == iso3][:6]
        lat, lon = COUNTRIES.centroid(iso3)
        holdings = [h["ticker"] for h in SETTINGS.watchlist.get("holdings", []) if iso3 in h.get("geographies", [])]
        return {"iso3": iso3, "name": COUNTRIES.name(iso3), "region": COUNTRIES.region(iso3), "lat": lat, "lon": lon,
                "euro_area": ea, "metrics": metrics, "derived": derived, "calendar": cal, "events": events[:15],
                "discrepancies": disc, "holdings": holdings, "as_of": as_of}

    # ------------------------------------------------------------ portfolio
    def portfolio(self, days: int = 7) -> List[dict]:
        ac = SETTINGS.readthrough.get("asset_commodity", {})
        since = (date.today() - timedelta(days=days)).isoformat()
        events = self.db.events(since=since, min_severity=3, limit=2000)
        out = []
        for h in SETTINGS.watchlist.get("holdings", []):
            geos, chokes = set(h.get("geographies", [])), set(h.get("chokepoints", []))
            comms = set(h.get("commodities", [])) | {ac[c] for c in h.get("commodities", []) if c in ac}
            hits = []
            for e in events:
                reasons = []
                if e.get("country") in geos:
                    reasons.append(f"geo {e['country']}")
                if e.get("entity") in chokes:
                    reasons.append(f"chokepoint {e['entity']}")
                ecomm = set(e.get("commodities") or [])
                for a in (e.get("readthrough") or {}).get("assets", []):
                    if a["asset"] in ac:
                        ecomm |= {a["asset"], ac[a["asset"]]}
                inter = comms & ecomm
                # commodity channel is broad: only rule alerts or severity >= 4 events count
                if inter and (e["kind"] == "ALERT" or e["severity"] >= 4):
                    reasons.append("exposure " + "/".join(sorted(inter)))
                if reasons:
                    hits.append({"id": e["id"], "kind": e["kind"], "title": e["title"], "severity": e["severity"],
                                 "ts": e["ts"], "reasons": reasons, "readthrough": e.get("readthrough")})
            hits.sort(key=lambda x: x["ts"], reverse=True)
            hits.sort(key=lambda x: -x["severity"])
            px = None
            for src in (["MARKET"] + (["DEMO"] if SETTINGS.demo_mode else [])):
                r = self.db.latest(f"EQ:{h['ticker']}", "price", src)
                if r:
                    px = self._stamp(_row(r))
                    break
            out.append(dict(h, price=px, hits=hits[:25], hit_count=len(hits),
                            max_severity=max([x["severity"] for x in hits], default=0)))
        return out

    # ======================================================= terminal functions
    def best_source(self, entity: str, metric: str):
        best = None
        for s in self.db.sources_for(entity, metric):
            if s == "DEMO" and not SETTINGS.demo_mode:
                continue
            r = self.db.latest(entity, metric, s)
            if r and (best is None or r["observation_date"][:19] > best["observation_date"][:19]):
                best = r
        return best

    def history(self, entity: str, metric: str, days: int, source: Optional[str] = None) -> dict:
        """GP: one source's daily history (never spliced across sources)."""
        r = self.db.latest(entity, metric, source) if source else self.best_source(entity, metric)
        if not r:
            return {"entity": entity, "metric": metric, "points": [], "status": "ERROR", "source": None}
        since = (_d(r["observation_date"]) - timedelta(days=days)).isoformat()
        rows = _daily(self.db.series(entity, metric, r["source"], since=since, limit=40000))
        d = self._stamp(_row(r))
        d["points"] = [[x["observation_date"][:10], x["value"]] for x in rows]
        d["sources"] = self.db.sources_for(entity, metric)
        return d

    def curve(self) -> dict:
        """GC: US Treasury constant-maturity curve now vs 1W / 1M / 1Y earlier."""
        from .securities import CURVE
        lat = [self.db.latest(ent, "yield", "FRED") for _, ent, _ in CURVE]
        dates = [r["observation_date"] for r in lat if r]
        if not dates:
            return {"curves": [], "spreads": [], "status": "ERROR"}
        anchor = _d(max(dates))
        curves = []
        for label, back in (("CURRENT", 0), ("1W AGO", 7), ("1M AGO", 30), ("1Y AGO", 365)):
            target = anchor - timedelta(days=back)
            pts = []
            for tenor, ent, yrs in CURVE:
                r = self.db.on_or_before(ent, "yield", "FRED", target.isoformat())
                ok = r is not None and (target - _d(r["observation_date"])).days <= 7
                pts.append({"tenor": tenor, "years": yrs, "entity": ent,
                            "value": r["value"] if ok else None, "date": r["observation_date"] if ok else None})
            curves.append({"label": label, "target": target.isoformat(), "points": pts})

        def val(c, t):
            return next((p["value"] for p in c["points"] if p["tenor"] == t), None)

        spreads = []
        for name, a, b in (("2s10s", "2Y", "10Y"), ("3m10y", "3M", "10Y"), ("5s30s", "5Y", "30Y"), ("2s5s", "2Y", "5Y")):
            row = {"name": name}
            for c in curves:
                x, y = val(c, a), val(c, b)
                row[c["label"]] = round((y - x) * 100, 1) if x is not None and y is not None else None
            spreads.append(row)
        status, age = self.st.status("FRED", "daily")
        return {"anchor": anchor.isoformat(), "curves": curves, "spreads": spreads, "status": status,
                "provider_age_s": age, "source": "FRED (H.15 constant maturity)"}

    def _eco_values(self, series: str, transform: str) -> dict:
        rows = self.db.series("US_ECO", series, "FRED", limit=60)
        v = [(r["observation_date"], r["value"], r["period_label"]) for r in rows]

        def calc(i):  # transform for the i-th row from the end (0 = latest)
            n = len(v) - 1 - i
            if n < 0:
                return None
            d, x, _ = v[n]
            if transform == "level":
                return x
            if n < 1:
                return None
            if transform == "chg":
                return x - v[n - 1][1]
            if transform == "mom":
                return (x / v[n - 1][1] - 1) * 100 if v[n - 1][1] else None
            if transform == "yoy":
                ya = f"{int(d[:4]) - 1}{d[4:10]}"
                ref = next((y for (dd, y, _) in v if dd[:10] == ya), None)
                return (x / ref - 1) * 100 if ref else None
            return None

        return {"latest": calc(0), "prior": calc(1),
                "period": v[-1][2] or (v[-1][0][:10] if v else None) if v else None,
                "prior_period": (v[-2][2] or v[-2][0][:10]) if len(v) > 1 else None,
                "obs_date": v[-1][0] if v else None}

    def eco(self) -> dict:
        """ECO: release calendar rows. LATEST is shown only when FRED's series was updated on
        or after the release date; otherwise AWAITING FRED (no stale number posing as new)."""
        cal = json.loads(self.db.kv_get("FRED:eco_calendar") or "[]")
        meta = json.loads(self.db.kv_get("FRED:eco_meta") or "{}")
        rels = {r["release_id"]: r for r in SETTINGS.eco.get("releases", [])}
        today = date.today().isoformat()
        last_past: Dict[int, str] = {}
        for c in cal:
            if c["date"] <= today:
                last_past[c["release_id"]] = max(last_past.get(c["release_id"], ""), c["date"])
        rows = []
        for c in sorted(cal, key=lambda x: x["date"]):
            rel = rels.get(c["release_id"])
            if not rel:
                continue
            for ind in rel["indicators"]:
                vals = self._eco_values(ind["series"], ind["transform"])
                upd = (meta.get(ind["series"]) or {}).get("last_updated") or ""
                row = {"date": c["date"], "release": rel["name"], "indicator": ind["label"], "series": ind["series"],
                       "transform": ind["transform"], "unit": ind.get("unit"), "fred_updated": upd,
                       "entity": "US_ECO", "metric": ind["series"]}
                if c["date"] > today:
                    row.update(status="SCHEDULED", prior=vals["latest"], prior_period=vals["period"], latest=None)
                elif c["date"] == last_past.get(c["release_id"]):
                    if upd[:10] >= c["date"]:
                        row.update(status="RELEASED", latest=vals["latest"], period=vals["period"],
                                   prior=vals["prior"], prior_period=vals["prior_period"])
                    else:
                        row.update(status="AWAITING FRED", prior=vals["latest"], prior_period=vals["period"], latest=None)
                else:
                    row.update(status="PAST", latest=None, prior=None)
                rows.append(row)
        horizon = (date.today() + timedelta(days=SETTINGS.eco.get("window_future_days", 45))).isoformat()
        past = (date.today() - timedelta(days=SETTINGS.eco.get("window_past_days", 10))).isoformat()
        for b in SETTINGS.cb_calendar.get("banks", []):
            r = self.db.latest(b["entity"], "policy_rate", "BIS")
            for m in b.get("meetings", []):
                if past <= m <= horizon:
                    rows.append({"date": m, "release": f"{b['bank']} decision", "indicator": "Policy rate",
                                 "status": "SCHEDULED" if m > today else "PAST",
                                 "prior": r["value"] if r else None, "prior_period": r["observation_date"] if r else None,
                                 "latest": None, "entity": b["entity"], "metric": "policy_rate",
                                 "calendar_verified": b.get("verified", False), "source_url": b.get("source_url")})
        rows.sort(key=lambda x: x["date"])
        return {"today": today, "rows": rows,
                "note": "Dates from FRED release calendar (no release times); CB meetings from config. "
                        "No consensus/survey column: no free source exists."}

    def des(self, entity: str, metric: str) -> dict:
        from .sanity import SanityChecker
        out = {"entity": entity, "metric": metric, "sources": []}
        for s in self.db.sources_for(entity, metric):
            r = self.db.latest(entity, metric, s)
            n = self.db._q("SELECT COUNT(*) AS n, MIN(observation_date) AS first FROM observations "
                           "WHERE entity=? AND metric=? AND source=?", (entity, metric, s))[0]
            out["sources"].append(self._stamp(dict(_row(r), count=n["n"], first=n["first"])))
        out["bounds"] = SanityChecker(SETTINGS.metrics.get("bounds", {})).rule_for(entity, metric)
        q = self.db._q("SELECT COUNT(*) AS n FROM quarantine WHERE entity=? AND metric=?", (entity, metric))
        out["quarantined"] = q[0]["n"]
        return out
