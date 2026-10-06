"""FastAPI app: REST + SSE. The browser only ever talks to this server."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analytics import Analytics
from .bus import BUS
from .db import DB
from .geo import COUNTRIES
from .scheduler import Scheduler
from .securities import CATALOG, parse as parse_command
from .news import news_store
from .vessels import km, store_for
from .settings import ROOT, SETTINGS
from .status import StatusResolver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs full request URLs at INFO, which would print API keys; /api/audit keeps a redacted record instead
logging.getLogger("httpx").setLevel(logging.WARNING)

db = DB(SETTINGS.db_path)
scheduler = Scheduler(db)
status = StatusResolver(scheduler.adapters, db.states)
an = Analytics(db, status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    BUS.bind(asyncio.get_running_loop())
    await scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="Riotto World Terminal", lifespan=lifespan)


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):
    resp = await call_next(request)
    # static assets revalidate (ETag) so a redeploy is picked up; API responses are never cached
    resp.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    return resp


def _as_of(as_of: Optional[str]) -> Optional[str]:
    if not as_of:
        return None
    try:
        d = date.fromisoformat(as_of[:10])
    except ValueError:
        raise HTTPException(400, "as_of must be YYYY-MM-DD")
    return None if d >= date.today() else d.isoformat()


@app.get("/api/meta")
def meta():
    visible = lambda xs: [{k: v for k, v in x.items() if k != "market"} for x in xs if an.shown(x)]
    return {"demo_mode": SETTINGS.demo_mode, "public_mode": SETTINGS.public_mode,
            "market_provider": SETTINGS.market_provider or None, "market_tier": SETTINGS.market_tier or None,
            "kpis": visible(SETTINGS.kpis["kpis"]), "commodities": visible(SETTINGS.kpis.get("commodities", [])),
            "choropleth": SETTINGS.metrics["choropleth"], "chokepoints": SETTINGS.chokepoints,
            "watchlist": [h["ticker"] for h in SETTINGS.watchlist.get("holdings", [])],
            "production_threshold_pct": SETTINGS.main["alerts"]["production_drop_mom_pct"],
            "providers": scheduler.health()}


@app.get("/healthz")
def healthz():
    """Liveness probe for the hosting platform (cheap; no provider calls)."""
    return {"ok": True}


@app.get("/api/credits")
def credits():
    return {**SETTINGS.credits, "public_mode": SETTINGS.public_mode,
            "hidden_series": sorted(SETTINGS.restricted_entities) if SETTINGS.public_mode else []}


@app.get("/api/health")
def health():
    return scheduler.health()


@app.post("/api/poll/{provider}")
async def poll_now(provider: str):
    """Manual refresh (respects the provider's rate limiter). Disabled on public deployments so
    visitors cannot spend the API budgets."""
    if SETTINGS.public_mode:
        raise HTTPException(403, "manual polling is disabled in PUBLIC_MODE")
    if provider not in scheduler.adapters:
        raise HTTPException(404, "unknown provider")
    ok, reason = scheduler.adapters[provider].enabled()
    if not ok:
        raise HTTPException(409, reason)
    return await scheduler.run_once(provider)


@app.get("/api/kpis")
def kpis():
    return {"kpis": an.kpis(), "commodities": an.commodities()}


@app.get("/api/kpi/{kid}")
def kpi(kid: str, days: int = Query(365, ge=7, le=3700)):
    d = an.kpi_history(kid.upper(), days)
    if not d:
        raise HTTPException(404, "unknown KPI")
    return d


@app.get("/api/choropleth")
def choropleth(metric: str, as_of: Optional[str] = None):
    if metric not in SETTINGS.metrics["choropleth"]:
        raise HTTPException(404, "unknown metric")
    return an.choropleth(metric, _as_of(as_of))


@app.get("/api/country/{iso3}")
def country(iso3: str, as_of: Optional[str] = None):
    iso3 = iso3.upper()
    if iso3 not in COUNTRIES.by_iso3:
        raise HTTPException(404, "unknown country")
    return an.country(iso3, _as_of(as_of))


@app.get("/api/production")
def production(as_of: Optional[str] = None):
    return an.production(_as_of(as_of))


@app.get("/api/chokepoints")
def chokepoints(as_of: Optional[str] = None):
    return an.chokepoints(_as_of(as_of))


@app.get("/api/macro")
def macro(as_of: Optional[str] = None):
    return an.macro_markers(_as_of(as_of))


@app.get("/api/infrastructure")
def infrastructure():
    return {"sites": SETTINGS.infra["sites"], "pipelines": SETTINGS.infra["pipelines"],
            "routes": SETTINGS.infra["routes"],
            "note": "Static reference: names and approximate locations only. Lanes are schematic and carry no volumes."}


@app.get("/api/events")
def events(as_of: Optional[str] = None, days: int = Query(7, ge=1, le=1900), min_severity: int = 1,
           kind: Optional[str] = None, region: Optional[str] = None, commodity: Optional[str] = None,
           source: Optional[str] = None, limit: int = Query(400, le=3000)):
    end = _as_of(as_of)
    since = ((date.fromisoformat(end) if end else date.today()) - timedelta(days=days)).isoformat()
    rows = db.events(since=since, until=end, kind=kind, min_severity=min_severity, limit=5000)
    out = []
    for e in rows:
        e["region"] = COUNTRIES.region(e.get("country"))
        e["country_name"] = COUNTRIES.name(e["country"]) if e.get("country") else None
        if region and region not in (e["region"], (COUNTRIES.by_iso3.get(e.get("country") or "") or {}).get("region")):
            continue
        if commodity and commodity not in (e.get("commodities") or []):
            continue
        if source and not (e.get("source") or "").startswith(source):
            continue
        out.append(e)
    # severity first, then recency
    out.sort(key=lambda e: e["ts"], reverse=True)
    out.sort(key=lambda e: -e["severity"])
    return out[:limit]


@app.get("/api/portfolio")
def portfolio(days: int = Query(7, ge=1, le=60)):
    return an.portfolio(days)


@app.get("/api/quarantine")
def quarantine(limit: int = 200):
    return db.quarantine_log(limit)


@app.get("/api/discrepancies")
def discrepancies(limit: int = 200):
    return db.discrepancies(limit)


@app.get("/api/audit")
def audit(provider: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.audit(provider, limit)


# ------------------------------------------------------------ terminal functions
@app.get("/api/securities")
def securities():
    return [s for s in CATALOG if not SETTINGS.hidden(s["entity"])]


@app.get("/api/command")
def command(q: str):
    r = parse_command(q)
    if r and "security" in r and SETTINGS.hidden(r["security"]["entity"]):
        r = None
    if r is None:
        raise HTTPException(404, f"no security matches {q!r}")
    if "error" in r:
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/history")
def history(entity: str, metric: str, days: int = Query(365, ge=7, le=4000), source: Optional[str] = None):
    if SETTINGS.hidden(entity):
        raise HTTPException(404, "not available on this deployment (licence)")
    return an.history(entity, metric, days, source)


@app.get("/api/curve")
def curve():
    return an.curve()


@app.get("/api/eco")
def eco():
    return an.eco()


@app.get("/api/des")
def des(entity: str, metric: str):
    if SETTINGS.hidden(entity):
        raise HTTPException(404, "not available on this deployment (licence)")
    return an.des(entity, metric)


# ------------------------------------------------------------ AIS tankers
_VCOMPACT = ("mmsi", "name", "lat", "lon", "sog", "cog", "heading", "size_class", "laden", "stale", "flag",
             "source", "age_s", "destination", "nav_text", "length", "draught", "moving", "pos_ts")


def _ais_status() -> dict:
    out = {}
    for prov in ("DIGITRAFFIC", "AISSTREAM"):
        st, age = status.status(prov, "intraday")
        h = next((x for x in scheduler.health() if x["provider"] == prov), {})
        out[prov] = {"status": st if h.get("enabled") else "DISABLED", "age_s": age, "reason": h.get("reason")}
    return out


@app.get("/api/vessels")
def vessels():
    vs = store_for(db).active()
    return {"vessels": [{k: v.get(k) for k in _VCOMPACT} for v in vs], "count": len(vs),
            "stale": sum(1 for v in vs if v["stale"]), "feeds": _ais_status(),
            "coverage": [b["name"] for b in SETTINGS.ais["aisstream_boxes"]],
            "note": "Terrestrial AIS, ship types 80-89 (crude/product/chemical/gas tankers). Dark vessels are invisible."}


@app.get("/api/vessel/{mmsi}")
def vessel(mmsi: int, track_hours: int = Query(72, ge=1, le=240)):
    v = store_for(db).get(mmsi, track_hours)
    if not v:
        raise HTTPException(404, "vessel not tracked")
    if v.get("lat") is not None:
        cps = sorted(((km(v["lat"], v["lon"], c["lat"], c["lon"]), c) for c in SETTINGS.chokepoints), key=lambda t: t[0])
        v["nearest_chokepoint"] = {"id": cps[0][1]["id"], "name": cps[0][1]["name"], "km": round(cps[0][0])}
        sites = sorted(((km(v["lat"], v["lon"], x["lat"], x["lon"]), x) for x in SETTINGS.infra["sites"]), key=lambda t: t[0])
        v["nearest_site"] = {"id": sites[0][1]["id"], "name": sites[0][1]["name"], "km": round(sites[0][0])}
    v["feeds"] = _ais_status()
    return v


@app.get("/api/vessels/near")
def vessels_near(lat: float, lon: float, km_radius: float = Query(50, gt=0, le=2000)):
    return [{k: v.get(k) for k in _VCOMPACT + ("dist_km",)} for v in store_for(db).near(lat, lon, km_radius)]


@app.get("/api/vessels/search")
def vessels_search(q: str):
    return [{k: v.get(k) for k in _VCOMPACT} for v in store_for(db).search(q)]


@app.get("/api/news")
def news(topic: Optional[str] = None, ticker: Optional[str] = None, q: Optional[str] = None,
         source: Optional[str] = None, hours: int = Query(48, ge=1, le=336), limit: int = Query(200, le=1000)):
    return news_store(db).query(topic and topic.upper(), ticker and ticker.upper(), q, source, hours, limit)


@app.get("/api/provider/{name}")
def provider(name: str):
    h = next((x for x in scheduler.health() if x["provider"] == name.upper()), None)
    if not h:
        raise HTTPException(404, "unknown provider")
    return dict(h, audit=db.audit(name.upper(), 20))


@app.get("/api/search")
def search(q: str):
    t = q.strip().upper()
    for k in SETTINGS.kpis["kpis"] + SETTINGS.kpis.get("commodities", []):
        if SETTINGS.hidden(k["entity"]):
            continue
        if t in (k["id"], k["label"].upper(), k["entity"]):
            return {"type": "kpi", "id": k["id"]}
    aliases = {"10Y": "UST10Y", "DXY": "DXY", "2S10S": "US2S10S", "HY": "HYOAS", "SPX": "SPX", "OAS": "HYOAS"}
    kid_entity = {k["id"]: k["entity"] for k in SETTINGS.kpis["kpis"]}
    if t in aliases and not SETTINGS.hidden(kid_entity.get(aliases[t], "")):
        return {"type": "kpi", "id": aliases[t]}
    for c in SETTINGS.chokepoints:
        if t in (c["id"], c["name"].upper()) or (len(t) >= 4 and t in c["name"].upper()):
            return {"type": "chokepoint", "id": c["id"]}
    for h in SETTINGS.watchlist.get("holdings", []):
        if t == h["ticker"]:
            return {"type": "holding", "id": h["ticker"]}
    metric_alias = {"GDP": "gdp_growth", "CPI": "cpi", "RATES": "policy_rate", "POLICY": "policy_rate",
                    "YIELD": "yield_10y", "OIL": "crude_prod_yoy", "PROD": "crude_prod_yoy", "FX": "fx_1m",
                    "CA": "current_account"}
    for m, cfg in SETTINGS.metrics["choropleth"].items():
        if t in (m.upper(), cfg["label"].upper()) or metric_alias.get(t) == m:
            return {"type": "metric", "id": m}
    iso3 = COUNTRIES.resolve(t)
    if iso3:
        return {"type": "country", "id": iso3}
    raise HTTPException(404, f"no match for {q!r}")


@app.get("/api/stream")
async def stream(request: Request):
    q = BUS.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            BUS.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


FRONT = ROOT / "frontend"


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


app.mount("/", StaticFiles(directory=str(FRONT)), name="static")
