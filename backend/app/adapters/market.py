"""Market price adapter  (PAID for real-time; free tiers are heavily throttled).

Plug-in point: MARKET_PROVIDER = twelvedata | finnhub | polygon in .env, symbols in
config/kpis.yaml (`market:` per KPI) and config/watchlist.yaml (`market:` per holding,
defaults to the ticker). Unknown or unentitled symbols are logged per symbol and
never filled.

Every quote is stored with observation_date = the provider's quote timestamp, so
"as of" reflects exchange time, not our fetch time.
"""
import math
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from ..httpclient import ProviderError, require
from ..schema import AdapterResult, Observation, utcnow_iso
from ..settings import SETTINGS
from .base import Adapter

# KPI entity -> unit for observations written by this adapter
_UNITS = {}
for _k in SETTINGS.kpis["kpis"] + SETTINGS.kpis.get("commodities", []):
    _UNITS[_k["entity"]] = _k["unit"]


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MarketAdapter(Adapter):
    name = "MARKET"
    free = False
    realtime = True
    description = "Intraday prices for KPIs + watchlist (Twelve Data / Finnhub / Polygon)"
    signup_url = "https://twelvedata.com/pricing | https://finnhub.io/register | https://polygon.io/dashboard/signup"

    def __init__(self, db):
        super().__init__(db)
        self.provider = SETTINGS.market_provider
        self.symbols = self._symbols()
        # Widen the interval so symbols x polls/day fits the daily budget.
        daily = self.cfg.get("daily_limit")
        if daily and self.symbols:
            min_interval = math.ceil(86400 * len(self.symbols) / daily)
            self.interval = max(self.interval, min_interval)
            self.stale_after = max(self.stale_after, self.interval * 3)

    def _symbols(self) -> List[Tuple[str, str, str]]:
        """[(provider_symbol, entity, metric)]"""
        out, seen = [], set()
        for k in SETTINGS.kpis["kpis"] + SETTINGS.kpis.get("commodities", []):
            if "MARKET" not in k.get("sources", []):
                continue
            sym = (k.get("market") or {}).get(self.provider)
            if sym and (sym, k["entity"]) not in seen:
                seen.add((sym, k["entity"]))
                out.append((sym, k["entity"], k["metric"]))
        for h in SETTINGS.watchlist.get("holdings", []):
            sym = (h.get("market") or {}).get(self.provider, h["ticker"])
            out.append((sym, f"EQ:{h['ticker']}", "price"))
        return out

    def enabled(self):
        if SETTINGS.public_mode and not SETTINGS.public_market_ok:
            return False, "PUBLIC_MODE: vendor market data is not redistributable (set MARKET_REDISTRIBUTION_LICENSED=true only if your plan allows it)"
        if not self.provider:
            return False, "MARKET_PROVIDER not set"
        key = {"twelvedata": SETTINGS.twelvedata_key, "finnhub": SETTINGS.finnhub_key,
               "polygon": SETTINGS.polygon_key}.get(self.provider)
        if key is None:
            return False, f"unknown MARKET_PROVIDER {self.provider!r}"
        if not key:
            return False, f"missing {self.provider.upper()}_API_KEY"
        return True, ""

    def _obs(self, entity: str, metric: str, value: float, ts: str, flags=None) -> Observation:
        unit = _UNITS.get(entity, "USD")
        return Observation(entity=entity, metric=metric, value=value, unit=unit, observation_date=ts,
                           source="MARKET", release_cadence="intraday",
                           flags=[f"{self.provider.upper()}"] + (flags or []))

    async def poll(self) -> AdapterResult:
        if self.provider == "twelvedata":
            return await self._twelvedata()
        if self.provider == "finnhub":
            return await self._finnhub()
        if self.provider == "polygon":
            return await self._polygon()
        raise ProviderError(f"unsupported provider {self.provider}", retryable=False)

    async def _twelvedata(self) -> AdapterResult:
        res = AdapterResult()
        syms = [s for s, _, _ in self.symbols]
        # each symbol costs 1 credit: batch in chunks that fit the per-minute allowance
        chunk = int(self.cfg.get("rate_per_min") or len(syms)) or 1
        quotes: Dict[str, dict] = {}
        for i in range(0, len(syms), chunk):
            part = syms[i:i + chunk]
            js = await self.http.get_json("https://api.twelvedata.com/quote",
                                          params={"symbol": ",".join(part), "apikey": SETTINGS.twelvedata_key},
                                          cost=len(part))
            if isinstance(js, dict) and js.get("status") == "error":
                raise ProviderError(f"twelvedata: {js.get('message')}", retryable=js.get("code") == 429)
            # single symbol -> flat dict; multiple -> {symbol: quote}
            quotes.update(js if len(part) > 1 else {part[0]: js})
        for sym, entity, metric in self.symbols:
            q = quotes.get(sym)
            if not isinstance(q, dict) or q.get("status") == "error" or "close" not in q:
                res.partial_errors += 1
                res.notes.append(f"{sym}: {(q or {}).get('message', 'no quote')}")
                continue
            ts = q.get("last_quote_at") or q.get("timestamp")
            require(ts is not None, f"twelvedata {sym}: no timestamp")
            res.observations.append(self._obs(entity, metric, float(q["close"]), _ts(float(ts))))
        if not res.observations:
            raise ProviderError("twelvedata returned no usable quotes: " + "; ".join(res.notes[:3]))
        return res

    async def _finnhub(self) -> AdapterResult:
        res = AdapterResult()
        for sym, entity, metric in self.symbols:
            try:
                q = await self.http.get_json("https://finnhub.io/api/v1/quote",
                                             params={"symbol": sym, "token": SETTINGS.finnhub_key})
            except ProviderError as exc:
                res.partial_errors += 1
                res.notes.append(f"{sym}: {exc}")
                continue
            # Finnhub returns zeros for unknown/unentitled symbols.
            if not isinstance(q, dict) or not q.get("t") or not q.get("c"):
                res.partial_errors += 1
                res.notes.append(f"{sym}: no data (unknown or not entitled)")
                continue
            res.observations.append(self._obs(entity, metric, float(q["c"]), _ts(q["t"])))
        if not res.observations:
            raise ProviderError("finnhub returned no usable quotes: " + "; ".join(res.notes[:3]))
        return res

    async def _polygon(self) -> AdapterResult:
        """Uses /v2/aggs/.../prev (works on the free tier, end-of-day). Paid tiers
        can switch to the snapshot endpoint for real-time."""
        res = AdapterResult()
        for sym, entity, metric in self.symbols:
            try:
                js = await self.http.get_json(f"https://api.polygon.io/v2/aggs/ticker/{sym}/prev",
                                              params={"adjusted": "true", "apiKey": SETTINGS.polygon_key})
            except ProviderError as exc:
                res.partial_errors += 1
                res.notes.append(f"{sym}: {exc}")
                continue
            results = (js or {}).get("results") or []
            if not results:
                res.partial_errors += 1
                res.notes.append(f"{sym}: no results")
                continue
            r = results[0]
            res.observations.append(self._obs(entity, metric, float(r["c"]), _ts(r["t"] / 1000.0), ["EOD"]))
        if not res.observations:
            raise ProviderError("polygon returned no usable quotes: " + "; ".join(res.notes[:3]))
        return res
