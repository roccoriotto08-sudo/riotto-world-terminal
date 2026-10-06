import asyncio

import pytest

from backend.app.adapters.base import Adapter
from backend.app.db import DB
from backend.app.scheduler import Scheduler
from backend.app.schema import AdapterResult


class StubAdapter(Adapter):
    name = "STUB"

    async def poll(self):
        res = AdapterResult()
        res.kv["STUB:cursor"] = "file-2026"
        return res


def test_cursor_not_committed_when_ingest_fails(tmp_path):
    db = DB(tmp_path / "t.db")
    s = Scheduler(db)
    s.adapters = {"STUB": StubAdapter(db)}

    def boom(*a, **k):
        raise RuntimeError("disk full")

    s.pipeline.ingest = boom
    with pytest.raises(RuntimeError):
        asyncio.run(s.run_once("STUB"))
    assert db.kv_get("STUB:cursor") is None      # the next poll re-fetches


def test_cursor_committed_after_ingest(tmp_path):
    db = DB(tmp_path / "t.db")
    s = Scheduler(db)
    s.adapters = {"STUB": StubAdapter(db)}
    asyncio.run(s.run_once("STUB"))
    assert db.kv_get("STUB:cursor") == "file-2026"
    assert db.states()["STUB"]["status"] == "OK"


def test_rate_limiter_rejects_impossible_cost():
    from backend.app.httpclient import ProviderError, RateLimiter
    lim = RateLimiter(per_min=8, daily=None)
    with pytest.raises(ProviderError):
        asyncio.run(lim.acquire(cost=17))


def test_daily_budget_enforced():
    from backend.app.httpclient import BudgetExhausted, RateLimiter
    lim = RateLimiter(per_min=None, daily=3)

    async def go():
        for _ in range(3):
            await lim.acquire()
        await lim.acquire()

    with pytest.raises(BudgetExhausted):
        asyncio.run(go())


def test_daily_budget_survives_restart(tmp_path):
    from backend.app.httpclient import BudgetExhausted, RateLimiter
    db = DB(tmp_path / "t.db")

    async def spend(n):
        lim = RateLimiter(per_min=None, daily=5, store=db, key="TD")   # fresh limiter = "restart"
        for _ in range(n):
            await lim.acquire()

    asyncio.run(spend(4))
    with pytest.raises(BudgetExhausted):
        asyncio.run(spend(2))
