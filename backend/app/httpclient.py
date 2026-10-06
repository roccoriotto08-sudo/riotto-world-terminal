"""Provider HTTP client: rate limiting, exponential backoff with jitter,
Retry-After handling, daily budget, and an audit row for every response."""
import asyncio
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from .db import DB
from .schema import utcnow_iso
from .settings import SETTINGS

_SECRET_PARAMS = re.compile(r"(api_key|apikey|token|key|password)=[^&]+", re.I)


class ProviderError(Exception):
    def __init__(self, msg: str, retryable: bool = True, status: Optional[int] = None):
        super().__init__(msg)
        self.retryable = retryable
        self.status = status


class BudgetExhausted(ProviderError):
    def __init__(self, msg: str):
        super().__init__(msg, retryable=False)


class RateLimiter:
    """Sliding-window per-minute limiter + optional UTC-day budget. The day count is
    persisted (kv) when a store is given, so restarts cannot overspend a daily quota."""

    def __init__(self, per_min: Optional[int], daily: Optional[int], store=None, key: str = ""):
        self.per_min = per_min
        self.daily = daily
        self.store = store
        self.key = key
        self.calls: list = []
        self.day = None
        self.day_count = 0
        self.lock: Optional[asyncio.Lock] = None   # created lazily inside the running loop (py3.9)

    async def acquire(self, cost: int = 1) -> None:
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            today = datetime.now(timezone.utc).date()
            if today != self.day:
                self.day, self.day_count = today, 0
                if self.store is not None and self.daily is not None:
                    self.day_count = int(self.store.kv_get(f"ratelimit:{self.key}:{today}") or 0)
            if self.daily is not None and self.day_count + cost > self.daily:
                raise BudgetExhausted(f"daily budget {self.daily} spent; resumes 00:00 UTC")
            if self.per_min:
                if cost > self.per_min:
                    raise ProviderError(f"request cost {cost} exceeds {self.per_min}/min limit", retryable=False)
                while True:
                    now = time.monotonic()
                    self.calls = [t for t in self.calls if now - t < 60]
                    if len(self.calls) + cost <= self.per_min:
                        break
                    await asyncio.sleep(60 - (now - self.calls[0]) + 0.05)
                self.calls.extend([time.monotonic()] * cost)
            self.day_count += cost
            if self.store is not None and self.daily is not None:
                self.store.kv_set(f"ratelimit:{self.key}:{today}", str(self.day_count))


class ProviderClient:
    def __init__(self, provider: str, db: DB, per_min: Optional[int] = None, daily: Optional[int] = None):
        h = SETTINGS.main["http"]
        self.provider = provider
        self.db = db
        self.limiter = RateLimiter(per_min, daily, store=db, key=provider)
        self.max_attempts = h["max_attempts"]
        self.base = h["backoff_base_s"]
        self.cap = h["backoff_cap_s"]
        self.client = httpx.AsyncClient(timeout=h["timeout_s"], follow_redirects=True,
                                        headers={"User-Agent": h["user_agent"]})

    def backoff(self, attempt: int) -> float:
        return min(self.cap, self.base * (2 ** attempt)) * (0.5 + random.random() / 2)

    async def request(self, method: str, url: str, *, params: Optional[Dict[str, Any]] = None,
                      data: Optional[dict] = None, headers: Optional[dict] = None,
                      cost: int = 1) -> httpx.Response:
        last_err: Optional[Exception] = None
        for attempt in range(self.max_attempts):
            await self.limiter.acquire(cost)
            requested = utcnow_iso()
            t0 = time.monotonic()
            status, err, resp = None, None, None
            try:
                resp = await self.client.request(method, url, params=params, data=data, headers=headers)
                status = resp.status_code
            except httpx.TransportError as exc:
                err = f"{type(exc).__name__}: {exc}"
            latency = int((time.monotonic() - t0) * 1000)
            safe_url = _SECRET_PARAMS.sub(r"\1=***", str(resp.request.url if resp is not None else url))
            self.db.log_response(self.provider, safe_url[:500], status, requested, utcnow_iso(), latency,
                                 resp.headers.get("date") if resp is not None else None,
                                 resp.headers.get("last-modified") if resp is not None else None,
                                 len(resp.content) if resp is not None else 0,
                                 err or (None if status and status < 400 else f"HTTP {status}"))
            if resp is None:
                last_err = ProviderError(err or "transport error")
            elif status == 429:
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff(attempt + 2)
                last_err = ProviderError(f"429 rate limited (retry-after {retry_after})", status=429)
                await asyncio.sleep(min(wait, 120))
                continue
            elif status >= 500:
                last_err = ProviderError(f"HTTP {status}", status=status)
            elif status >= 400:
                raise ProviderError(f"HTTP {status}: {resp.text[:200]}", retryable=False, status=status)
            else:
                return resp
            if attempt < self.max_attempts - 1:
                await asyncio.sleep(self.backoff(attempt))
        raise last_err or ProviderError("request failed")

    async def get_json(self, url: str, **kw) -> Any:
        resp = await self.request("GET", url, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"invalid JSON from {self.provider}: {exc}", retryable=False)

    async def get_text(self, url: str, **kw) -> str:
        return (await self.request("GET", url, **kw)).text

    async def get_bytes(self, url: str, **kw) -> bytes:
        return (await self.request("GET", url, **kw)).content

    async def close(self) -> None:
        await self.client.aclose()


class SchemaError(ProviderError):
    """Provider payload did not match the expected shape."""

    def __init__(self, msg: str):
        super().__init__(msg, retryable=False)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)
