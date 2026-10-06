# RIOTTO//WORLD TERMINAL

**A Bloomberg-style global macro and supply-shock terminal, built around a live 3D globe.**

**Live demo:** _add your URL here after deploying (see [DEPLOY.md](DEPLOY.md))_

<!-- Add a screenshot: save one as docs/screenshot.png and uncomment the next line -->
<!-- ![Riotto World Terminal](docs/screenshot.png) -->

Riotto World Terminal tracks the physical and macro drivers of markets in one screen. It shows oil
output by country, shipping chokepoints and live tanker positions, central-bank rates, the Treasury
curve, economic releases, geopolitical events and a live news wire. Everything sits in draggable,
resizable workspaces with a Bloomberg-style command line (`CL1 COMDTY GP <GO>`, `GC`, `ECO`, `TOP`).

**Highlights**
- **Globe**
  - oil output, shipping chokepoints with 7-day vs 90-day transit disruption scoring
  - live AIS tankers drawn as animated 3D ships
  - pipelines, refineries and LNG terminals
  - central-bank rates and geopolitical/weather events
  - a time scrubber to replay the map
- **Markets:** a KPI monitor, price/level charts with studies and comparisons (GP), the Treasury curve
  vs 1W/1M/1Y ago (GC), and a US release calendar that won't show a stale value as a new print (ECO)
- **Signals:** a config-driven alert engine (output drops, chokepoint disruptions, policy-rate moves,
  curve inversion). Every alert stores the exact data points behind it and a market read-through.
- **Live news wire** from 17 publisher feeds, tagged by topic and by watchlist ticker
- **Data integrity:**
  - every value shows its source, observation date and LIVE / DELAYED / STALE status
  - unit normalization with tests, sanity bounds with a quarantine log
  - cross-source checks (EIA vs JODI) and a full provider audit log
  - nothing is ever fabricated or forward-filled

**Stack:** Python (FastAPI, httpx, pydantic, SQLite) with 14 provider adapters, each with its own rate
limits, backoff and schema checks. The front end uses vanilla ES modules, globe.gl / three.js (instanced
3D ships) and GridStack. There is no build step, and the 57 unit tests run on every start.

**Run locally:** `./start.sh`, then open http://localhost:8700. **Deploy:** see [DEPLOY.md](DEPLOY.md).

---

## Overview (technical)

A Bloomberg-style terminal centred on a 3D globe: oil & gas output, chokepoint transits,
central-bank rates, cross-asset KPIs, geopolitical / weather events, a config-driven alert
engine with market read-throughs, and a portfolio overlay. **Every number on screen comes
from a real provider** and carries its observation date, source and LIVE / DELAYED / STALE /
ERROR status. No mock data runs unless you set `DEMO_MODE=true` (permanent red banner).

## Free vs paid feeds — what you lose without the paid one

| Feed | Cost | Key | Powers | If omitted |
|---|---|---|---|---|
| **World Bank WDI** | free | none | GDP growth, CPI, current-account choropleths (annual) | — |
| **BIS WS_CBPOL** | free | none | Policy rates (~40 central banks, daily), rate-change alerts | — |
| **JODI-Oil** | free | none | Monthly crude output by country (kb/d), m/m-drop alerts, y/y choropleth | — |
| **IMF PortWatch** | free | none | Daily chokepoint transits, disruption status, flow-drop alerts | — |
| **GDELT 2.0** | free | none | 15-min geocoded conflict / coercion / sanctions / protest events | — |
| **GDACS** | free | none | Orange/red cyclone, flood, quake, drought alerts (weather layer) | — |
| **Digitraffic (Fintraffic)** | free | none | Live AIS tankers in the Baltic / Gulf of Finland (Primorsk, Ust-Luga lanes) | — |
| **Publisher RSS (17 feeds)** | free | none | Live news wire: BBC, Al Jazeera, NPR, Guardian, Defense News, OilPrice, CNBC (top/markets/energy), EIA, gCaptain, Hellenic Shipping, MarketWatch, Yahoo Finance, Seeking Alpha, Investing.com, Federal Reserve | — |
| **AISStream.io** | free | `AISSTREAM_API_KEY` | Live AIS tankers at Hormuz, Red Sea, Suez, Malacca, US Gulf, North Sea, Black Sea, China coast, Cape, Panama, W. India | Tanker layer covers the Baltic only |
| **FRED** | free | `FRED_API_KEY` | UST 10Y/2Y, 2s10s, HY OAS, VIX, USD broad index, USDJPY, SPX, WTI/Brent/HH daily closes, IMF monthly TTF/copper/uranium/Asia LNG, FX, OECD 10Y yields | KPI strip is empty except via a market feed; no 10Y or FX choropleths; no curve alerts |
| **EIA API v2** | free | `EIA_API_KEY` | Weekly petroleum status (US output, commercial + SPR stocks), gas storage, international + STEO production | No EIA-vs-JODI cross-check, no WPSR draw alerts, no STEO estimates |
| **ACLED** | free (non-commercial; licence for commercial use) | `ACLED_EMAIL` / `ACLED_PASSWORD` | Curated conflict events with fatalities | Conflict layer relies on GDELT alone (noisier, machine-coded) |
| **Market prices** (Twelve Data / Finnhub / Polygon) | **paid** for real-time | `MARKET_PROVIDER` + key | Intraday WTI/Brent/USD/VIX/copper/gold/USDJPY/SPX, watchlist prices | KPIs fall back to FRED end-of-day closes (T-1, some weekly-lagged), **gold shows NO DATA** (no free daily gold series exists on FRED), TTF/copper are IMF *monthly averages*, no live P&L context. Free tiers: Twelve Data 800 credits/day (≈31 min refresh for 17 symbols), Polygon end-of-day only, Finnhub free has no FX/commodity quotes |
| OPEC+ quotas | free but **manual** | — | Quota vs actual in the production panel | Quota column hidden. OPEC publishes only PDFs; edit `config/reference/opec_quotas.yaml` |

Never available for free here: JKM (proxy = IMF Japan LNG import price, monthly), ICE DXY (proxy =
Fed broad USD index — labelled as such), refinery/pipeline outages feed, real-time tanker AIS.

## Architecture

```
 providers ──► adapters (one per feed, own poll interval, rate limiter, backoff, schema checks)
                 │  native units
                 ▼
            pipeline: unit normalization ─► sanity bounds/quarantine ─► SQLite (history + revisions)
                 │                         └► EIA-vs-JODI cross-check ─► discrepancy log
                 ├► rules engine (new data only) ─► ALERT events with trigger data points + read-through
                 └► SSE bus  ──►  browser (globe.gl + DOM panels; patches only what changed)
 FastAPI REST (/api/*) serves read models; status (LIVE/DELAYED/STALE/ERROR) computed at read time
 from provider health, so a value can never look fresh after its feed dies.
```

* Backend: Python 3.9+ / FastAPI / httpx / SQLite (WAL). Keys live only in `.env` on the server.
* Frontend: static ES modules + vendored `globe.gl` (three.js). No build step, no CDN at runtime.

## Run it

```bash
./start.sh
```

Creates `.venv`, installs deps, copies `.env.example` → `.env` on first run, runs the unit tests,
then serves http://127.0.0.1:8700. The keyless feeds work immediately. Add keys to `.env` and restart
to enable FRED / EIA / ACLED / market data. The first run backfills history (≈1–2 min, JODI is the
slowest: ~60 MB of CSV) so the 1M/3M/1Y/5Y scrubber has data.

`DEMO_MODE=true ./start.sh` adds synthetic KPI and watchlist prices for UI work. They are tagged `DEMO`
everywhere and a red banner stays on screen.

## Poll schedule & rate-limit budget

Source of truth: `config/settings.yaml`. "Self-limit" is the client-side ceiling enforced by
`httpclient.RateLimiter`. A request that cannot fit fails fast instead of hanging.

| Provider | Poll | Requests per poll | ≈ Requests/day | Provider limit | Self-limit | Stale after |
|---|---|---|---|---|---|---|
| Market — Twelve Data free | auto-widened to fit budget (≈31 min for 17 symbols) | 17 credits in chunks of 8 | ≤800 credits | 8/min, 800/day | 8/min, 800/day (then `BUDGET` until 00:00 UTC) | 3× interval |
| Market — Twelve Data paid | 15 s | 17 credits | ~98k | plan-dependent | 600/min | 3 min |
| Market — Finnhub free | 15 s | 10 (40/min) | ~58k | 60/min | 55/min | 3 min |
| Market — Polygon free | 60 s (throttled to ≈2.5 min) | 12 | ~7k | 5/min | 5/min | 3 min |
| FRED | 1 h | 80 series + 10 release calendars + 12 series metadata | ~2,500 | 120/min | 100/min | 3 h |
| EIA v2 | 1 h | 6 | ~150 | not published (fair use) | 30/min | 3 h |
| JODI | 24 h | 1 page + 1–2 CSV (skipped if Last-Modified unchanged) | ≤3 | n/a | 10/min | 3 d |
| World Bank | 24 h | 3 | 3 | n/a | 30/min | 3 d |
| BIS | 6 h | 1 | 4 | n/a | 10/min | 24 h |
| IMF PortWatch | 6 h | 1–2 pages (≈57 on first run) | ~8 | ArcGIS 1000 rows/page | 20/min | 24 h |
| GDELT | 15 min | 1 index + 1 zip (catches up ≤8 missed files) | ~192 | n/a | 20/min | 1 h |
| GDACS | 30 min | 1 | 48 | n/a | 10/min | 2 h |
| News RSS | 2 min | 17 feeds | ~12,000 | publisher fair use | 30/min | 15 min |
| Digitraffic | 60 s | 2 | ~2,900 | fair use (send Digitraffic-User header) | 10/min | 10 min |
| AISStream | websocket, 15-min sessions | 1 connection | — | 3 connections/account, 1 sub update/s | — | 10 min |
| ACLED | 6 h | 1 token (cached 24 h) + 1–2 pages | ~8 | account-dependent | 10/min | 24 h |

Retries: each request retries up to 4× with exponential backoff and jitter (2 s → 60 s cap),
honours `Retry-After` on 429, and never retries 4xx. A failed poll cycle backs off
30 s × 2ⁿ (capped at the normal interval) and the health matrix turns red.

## Data integrity rules (what the UI guarantees)

* **Normalized schema** for every value: `{entity, metric, value, unit, observation_date, fetched_at,
  source, release_cadence, status}` (+ provenance `flags`).
* **As-of everywhere.** Every KPI, panel row and tooltip shows the observation date and source.
  Production shows the *reference month* (e.g. `2026-07`); chokepoints show the AIS day (~1 week lag).
* **STALE, never invented.** If a feed fails, the last good value stays on screen with a `STALE` badge
  and the age since the last successful fetch. `ERROR` means nothing has ever arrived (the cell says which
  key or feed is missing).
* **No interpolation / forward-fill.** FRED "." days are skipped. Derived numbers (y/y, 1M FX change,
  10Y − policy) are flagged `DERIVED` and show their basis. Euro-area aggregates painted on members are
  flagged `EURO_AREA`; inverted FX quotes `INVERTED`; STEO model values `ESTIMATE`; JODI unassessed
  rows `UNASSESSED`; manual config `MANUAL`; provider revisions `REVISED`.
* **Units**: `backend/app/units.py` maps every published unit to one canonical unit per dimension
  (kb/d, mb, bcf, bcf/d). EIA's "MBBL" (= thousand) vs market "mb/d" (= million) is explicit and tested.
  Unknown units are quarantined, never guessed.
* **Sanity bounds + quarantine** (`config/metrics.yaml`): e.g. Brent must be > 0 and move < 50 % vs the
  previous print. Rejected rows go to `/api/quarantine`. A genuine regime change (Argentina's 133 → 100 %
  policy-rate cut) is accepted once a *second* consecutive print confirms the new level, and flagged
  `LEVEL_SHIFT`.
* **Cross-check**: EIA vs JODI monthly output for the same country-month; gaps ≥ 5 % are logged
  (`/api/discrepancies`) and badged `XCHK` in the production table.
* **Audit**: every provider response (status, latency, provider `Date` / `Last-Modified`, bytes,
  error) is stored. See `/api/audit`. Keys are redacted from logged URLs.
* **Cursor safety**: adapters return their "already fetched" cursors with the data. They are committed
  only after the data is stored, so an interrupted poll re-fetches instead of silently skipping.
* **Replay caveat**: the time scrubber selects the latest observation *period* on or before the date.
  It does not reconstruct as-published vintages, so later revisions and publication lags are not
  modelled. (Plug-in point: ALFRED for point-in-time FRED.)

## Alerts & read-throughs

Rules live in `config/rules.yaml` (`pct_change`, `abs_change`, `baseline_ratio`, `level_and_change`).
Shipped rules: crude output m/m ≤ −3 % (matches the pulsing-marker threshold), chokepoint 7d transits
≤ −35 % vs prior 90d, any policy-rate change, US 2s10s inversion deepening ≥ 10 bp w/w, Brent ±5 % d/d,
large US crude draw.

* Rules evaluate **only on newly ingested observations** within `fresh_days`, so history backfills never
  fire. Each alert stores the exact rows it used (`trigger`) and is deduplicated by rule/entity/period.
* Every alert and event carries a one-line read-through: affected assets, direction and a probability `p`.
  **`p` is an analyst-set prior in config, not a model output**, and the UI labels it as such. Edit
  `config/rules.yaml` (alerts) and `config/readthrough.yaml` (events by country/category, plus a
  chokepoint-proximity rule for incidents at sea).

## Portfolio overlay

`config/watchlist.yaml` maps each ticker to geographies (ISO3), commodities and chokepoints (the shipped
book is an **example**: replace `[TICKERS]`). Layer 7 outlines exposed countries in cyan. The left rail
lists holdings with 7-day hit counts. Clicking a holding shows which alerts/events hit it and why
(geography / chokepoint / commodity exposure; the commodity channel counts only rule alerts and sev ≥ 4).

## Live news (`TOP`, `N <topic>`, `<ticker> CN`, NEWS tab)

Seventeen publisher RSS feeds (`config/news.yaml`) are polled every 2 minutes. New headlines are pushed
live to the bottom NEWS tab (with an unread badge), to any open news panel (flashing), and to the
command-line ticker when a headline is HOT.

* **Stored and shown:** only the headline, source, publication time and link. Article text is never
  stored; clicking opens the publisher's page.
* **Undated items are dropped** instead of being given a time.
* **Topic tags** (WAR, OIL, SHIPPING, EQUITIES, MACRO, plus WORLD/MARKETS feed defaults) come from
  keyword rules.
* **Watchlist tickers** are tagged only from company names (case-sensitive) or explicit forms like
  `$XOM`, `(XOM)` and `NYSE: XOM`. So "Freeport LNG" does not tag FCX, and "Asian LNG prices" does not
  tag Cheniere.
* **Deduplication:** identical headlines from several feeds are kept once.
* **Retention:** 14 days.

Where news appears:
* the right-rail home (top 8 headlines from the last 12 h, ranked by HOT / war / oil / watchlist)
* each holding's panel (company news)
* `TOP` / `N OIL` / `NEWS WAR` in a news panel (topic tabs and search)
* `XOM US EQUITY CN`

## Tankers (layer 8, live AIS)

Every AIS ship of type 80–89 (tankers: crude, product, chemical and most gas carriers; AIS does not
separate them) seen by the feeds is drawn as an animated 3D tanker.

* **Model:** a hull with a raked bow and an accommodation block at the stern, pointed along the AIS true
  heading (or course over ground). It has a gentle roll and, when under way, a shimmering wake whose
  length scales with speed.
* **Drawing cost:** all ships draw in 3 instanced calls (`frontend/js/ships.js`, using a vendored
  three.js r185 that matches globe.gl's).
* **Positions:** a ship that is under way with a report under 30 minutes old is advanced along its
  reported course at its reported speed (dead reckoning, capped at 30 minutes), and its tooltip says
  "position est. +N min". Every other ship sits exactly on its last reported position. Roll, bob and
  wake are cosmetic.

Hull colour:

| Colour | Meaning |
|---|---|
| orange | under way, likely laden |
| cyan | under way, ballast or unknown |
| yellow | anchored or moored |
| grey | last report more than 2 h old |

Larger dots are VLCC/Suezmax. Hover for a summary; click for the full card: name, MMSI/IMO, flag
(from the MMSI), size class, speed/course, navigation status, self-reported destination and ETA,
draught, nearest chokepoint and terminal, and a 72-hour track drawn on the globe. The command line also
finds tankers by name, MMSI or IMO. Chokepoint, terminal and ocean-location panels list the tankers
currently nearby.

Sources:
* **Digitraffic** (no key, polled every 60 s): Baltic.
* **AISStream.io** (free key): a websocket covering the boxes in `config/ais.yaml`. Positions are held
  until the ship's static broadcast (about every 6 min) identifies it as a tanker, so the layer fills in
  over the first ~10 minutes.

Integrity checks: positions at 0,0, speeds above 40 kn, and jumps implying more than 60 kn are rejected.
Tracks are sampled every 15 min and kept for 10 days.

Honest limits:
* This is terrestrial AIS. Coverage is good near coasts and straits and thin mid-ocean.
* Tankers that switch AIS off (the "dark fleet") are invisible.
* Size class, flag and laden/ballast are heuristics from AIS fields. Crews often leave draught and
  destination stale.
* Tankers are live-only, so they are hidden during time replay.

## Click anything

Every object on the globe and in the panels opens a detail view:

* **Globe objects:** countries, tankers, chokepoints, production labels, central-bank labels, commodity
  hubs, events and alerts, refineries/terminals/LNG/SPR sites (with tankers within 40 km), pipelines,
  shipping lanes (with live chokepoint states). Empty ocean opens nearest chokepoints, tankers and events.
* **Header and strips:** ticker-tape items, KPI cells, and each data-health cell (with that feed's last
  responses).
* **Panel rows:** country KPI rows (open a chart), and feed / ECO / GC rows.

## Workspaces & panels

The screen is a grid of panels on workspace tabs, in the style of Robinhood Legend.
Defaults: **MAIN**, **MARKETS**, **ENERGY**, **GEO**.

* **Tabs:**
  * `+` adds a workspace and lets you name it.
  * Double-click a tab to rename it.
  * Click its `×` twice to delete it.
  * `RESET` (click twice) restores a default workspace.
* **Panels:**
  * Drag a panel by its header, resize from its edges and corners, or maximize it (⤢, double-click
    the header, or `MAX`). `Esc` restores.
  * `×` closes a panel.
  * `UNLOCKED` / `LOCKED` freezes the layout.
* **+ PANEL** adds any panel:
  * live components: globe, KPI monitor, inspector, events & news wire, tankers, chokepoints, crude
    output, commodities, portfolio
  * as many news panels (any topic) and function panels (GP charts, GC, ECO, HELP …) as you like
* **Commands** open in the focused function panel, or in a new one if none is focused. `ADD TANKERS`
  adds a panel, `TAB MARKETS` switches workspace.
* **Persistence:** layouts are saved per browser (localStorage).
* **Live components** exist once. Adding one to another workspace moves it there, so switching tabs
  takes the globe with you; the globe stops drawing whenever it isn't on screen.
* Clicking anything opens the **inspector**; if the current workspace has none, one is added.

## Terminal functions (Bloomberg-style)

Grammar: `<TICKER> <YELLOW KEY> <FUNCTION> <GO>` (Enter = GO). The yellow key is optional; a wrong one
is an error, never a guess (`CL1 GOVT` → "CL1 is not a GOVT security"). A bare ticker opens a numbered
menu: type `2 <GO>` to pick item 2. Yellow keys: **F2 GOVT · F8 EQUITY · F9 COMDTY · F10 INDEX ·
F11 CRNCY** (hold `fn` on a Mac), or click the amber chips next to the command line.

| Function | What it shows | Example |
|---|---|---|
| `GP` | Chart: 1M–MAX windows, MA20/50/200, Bollinger, log scale, crosshair readout, up to 3 compares (rebased to 100) | `CL1 COMDTY GP`, then type `CO1` in the compare box |
| `DES` | Provenance: every source holding the series, coverage, first/last obs, status, flags, sanity bounds, quarantined prints | `USGG10YR GOVT DES` |
| `GC` | US Treasury constant-maturity curve now vs 1W/1M/1Y ago, Δ in bp, 2s10s/3m10y/5s30s/2s5s; `n <GO>` charts a tenor | `GC` |
| `ECO` | US release calendar (FRED) + central-bank meetings (config): PRIOR / LATEST / status | `ECO` |
| `MENU` / `Esc` | Back in the focused panel | |
| `HELP` | Syntax and ticker list | |

Tickers (`config/securities.yaml`, plus generated entries): COMDTY `CL1 CO1 NG1 TTF JKM HG1 GC1 UX1` ·
GOVT `USGG1M … USGG30YR`, `DEU10Y GBR10Y JPN10Y …` · INDEX `SPX VIX USYC2Y10 LF98OAS CPIAUCSL PAYEMS UNRATE ICSA …` ·
CRNCY `USDJPY USDEUR USDCNY … DXY` · EQUITY watchlist names (`XOM US`).

Honest limits:
* ECO has **no consensus/survey column** (there is no free source) and **no release times** (FRED
  publishes dates only).
* A row is `RELEASED` only once FRED's series `last_updated` is on or after the release date. Until then it
  shows `AWAITING FRED`, so a stale number never looks like the fresh print.
* FX tickers are always quoted local-per-USD, matching what is stored. EUR/GBP/AUD are therefore `USDEUR`
  etc., and the market-convention `EURUSD` is deliberately not offered.
* Not available on free data: WEI, futures curves, intraday charts, news (`TOP`/`N`). Typing them says so.

## Using the terminal

| Input | Action |
|---|---|
| `/` | focus the command line |
| `CL1 COMDTY GP`, `GC`, `ECO` … | terminal functions (see above) |
| `ADD WIRE`, `TAB ENERGY`, `MAX` | add a panel · switch workspace · maximize the focused panel |
| `CHN`, `GERMANY`, `BR` | focus a country, open its panel (KPIs + sparklines, CB calendar, events, exposure) |
| `WTI`, `BRENT`, `VIX`, `10Y`, `2S10S`, `HY` | open KPI detail (history 1M/3M/1Y/5Y) |
| `HORMUZ`, `SUEZ`, `MALACCA` … | chokepoint detail (7d vs 90d, 120-day chart) |
| `XOM` (any watchlist ticker) | holding detail |
| `GDP`, `CPI`, `RATES`, `YIELD`, `OIL`, `FX`, `CA` | switch the choropleth |
| `TOP`, `N OIL`, `NEWS WAR`, `XOM US EQUITY CN` | live news wire / company news |
| `1`–`8` | toggle layers (oil, chokepoints+lanes, infrastructure, macro/CB, commodity hubs, events, portfolio, tankers) |
| tanker name / MMSI / IMO | find a tanker (e.g. `BREEZE`) |
| `R` | reset view · `Esc` close panel · `HEALTH` data-health detail · `LIVE` exit replay |

The header shows a scrolling tape, the UTC clock, Tokyo/London/NY regular sessions (exchange holidays
are not modelled), and the data-health matrix (hover for last success / next poll / last error; click
for the full table, quarantine and discrepancy logs).

## Repo structure

```
macro-globe/
├── start.sh                    one-command start (venv, deps, tests, server)
├── requirements.txt
├── .env.example                every key, with sign-up URLs
├── config/
│   ├── settings.yaml           poll intervals, rate limits, staleness, thresholds
│   ├── kpis.yaml               KPI strip + commodity panel; market symbols per provider
│   ├── fred.yaml               FRED series → (entity, metric, unit)
│   ├── metrics.yaml            sanity bounds + choropleth metric definitions ([YOUR METRIC] slot)
│   ├── rules.yaml              alert rules + read-through priors
│   ├── readthrough.yaml        event → asset read-through map, chokepoint proximity
│   ├── watchlist.yaml          portfolio overlay ([TICKERS])
│   ├── securities.yaml         Bloomberg-style ticker aliases (CL1, CO1, USGG10YR …)
│   ├── eco.yaml                ECO release list, indicators and transforms
│   ├── news.yaml               news feeds, topic keyword rules, HOT words
│   ├── ais.yaml                tanker size classes, staleness, AISStream regions
│   └── reference/              static geography: chokepoints, infrastructure, lanes, OPEC quotas, CB calendar
├── backend/
│   ├── app/
│   │   ├── main.py             FastAPI REST + SSE + static
│   │   ├── scheduler.py        per-adapter loops, cycle backoff, health
│   │   ├── pipeline.py         normalize → sanity → store → cross-check → alerts → publish
│   │   ├── alerts.py           rules engine + event read-through annotation
│   │   ├── analytics.py        read models (KPIs, choropleth, production, chokepoints, country, portfolio)
│   │   ├── status.py           LIVE/DELAYED/STALE/ERROR at read time
│   │   ├── httpclient.py       rate limiter, backoff, Retry-After, audit log
│   │   ├── securities.py       ticker catalog + "<TICKER> <YELLOW KEY> <FUNCTION>" parser
│   │   ├── units.py · sanity.py · schema.py · db.py · geo.py · bus.py · settings.py
│   │   └── adapters/           ◄── plug-in point: one file per provider (see adapters/base.py)
│   │       fred.py eia.py market.py jodi.py worldbank.py bis.py portwatch.py
│   │       gdelt.py gdacs.py acled.py opec_cfg.py demo.py digitraffic.py aisstream.py news_rss.py
│   │   ├── news.py             news store: RSS/Atom parsing, topic + ticker tagging, dedupe
│   │   ├── vessels.py          AIS tanker store: sanity checks, size class, flag (MID), ETA decode, tracks
│   └── tests/                  units, sanity/quarantine, rules, parsers, scheduler cursor safety
├── frontend/
│   ├── index.html · css/terminal.css
│   ├── js/app.js (state, panels, feed, scrubber, commands) · js/globe.js (globe.gl layers) · js/util.js
│   ├── js/dashboard.js (workspace tabs + draggable/resizable/maximizable panels, persistence)
│   ├── js/functions.js (GP, GC, ECO, DES, MENU, HELP, NEWS/CN)
│   ├── js/ships.js (animated instanced 3D tankers, dead reckoning)
│   ├── data/countries.geojson  Natural Earth 110m (public domain), trimmed
│   └── vendor/                 globe.gl 2.46.2, three.js r185 (matches globe.gl), GridStack 14.0.0 — all vendored
└── data/                       SQLite database (created at runtime)
```

## Adding a provider

1. Create `backend/app/adapters/<name>.py` subclassing `Adapter`. Set `name`, `free`, `requires`
   (settings attributes that must be non-empty) and `description`.
2. Implement `async poll() -> AdapterResult`. Return `Observation`s in the provider's **native units**
   (the pipeline normalizes them). Raise `SchemaError` via `require(...)` on unexpected payloads. Put
   "already fetched" cursors in `res.kv`.
3. Register it in `adapters/__init__.py` and add its schedule to `config/settings.yaml`.
4. Add sanity bounds for any new metric to `config/metrics.yaml`.

## What was verified, and what wasn't

* Verified live (2026-10-05) against the real endpoints: World Bank, BIS, JODI, IMF PortWatch, GDELT,
  GDACS. A cold start ingests ~70k observations and fires two genuine alerts: Strait of Hormuz 7-day
  transits −64 % vs the 90-day baseline (3.1/day vs 8.7/day, as of 2026-09-27), and Norges Bank +25 bp
  to 4.50 %.
* FRED verified live with a key: 80 series, the release calendar and series metadata.
* Rendering: with all seven layers on, the globe costs about 6.6 ms of GPU time per frame (632 draw calls)
  after hiding the invisible country side walls (was ~15 ms), leaving headroom for 60 fps.
* Every FRED series ID in `config/fred.yaml` was checked to exist. Not available: OECD 10Y for China,
  India, Brazil and Colombia, and LBMA gold (discontinued). Those cells show NO DATA.
* **Not exercised end-to-end without credentials:** FRED, EIA, ACLED and the three market providers.
  Their adapters follow the providers' documented APIs and fail loudly (health matrix + `/api/audit`)
  if a payload differs. Confirm on first run with your keys:
  * EIA STEO series IDs (`COPR_*`)
  * ACLED's OAuth flow
  * which `config/kpis.yaml` market symbols your plan includes
* Not built: the optional data-centre/power layer (key `7` drives the portfolio overlay instead), an
  outages feed, exchange-holiday calendars, and point-in-time vintages.
