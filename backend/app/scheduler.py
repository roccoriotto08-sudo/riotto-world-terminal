"""Runs every enabled adapter on its own schedule with cycle-level exponential backoff."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .adapters import ALL
from .bus import BUS
from .db import DB
from .httpclient import BudgetExhausted
from .pipeline import Pipeline
from .schema import utcnow_iso

log = logging.getLogger("scheduler")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Scheduler:
    def __init__(self, db: DB):
        self.db = db
        self.pipeline = Pipeline(db)
        self.adapters = {cls.name: cls(db) for cls in ALL}
        self.tasks = []
        self.locks = {}

    def health(self) -> list:
        states = self.db.states()
        out = []
        for name, a in self.adapters.items():
            st = states.get(name, {})
            out.append({"provider": name, "free": a.free, "description": a.description,
                        "signup_url": a.signup_url, "interval_s": a.interval, "stale_after_s": a.stale_after,
                        "enabled": bool(st.get("enabled")), "reason": st.get("reason"),
                        "status": st.get("status") or "PENDING", "last_attempt": st.get("last_attempt"),
                        "last_success": st.get("last_success"), "next_poll": st.get("next_poll"),
                        "consecutive_failures": st.get("consecutive_failures") or 0,
                        "last_error": st.get("last_error"), "last_count": st.get("last_count"),
                        "notes": st.get("notes")})
        return out

    async def start(self) -> None:
        for i, (name, a) in enumerate(self.adapters.items()):
            ok, reason = a.enabled()
            self.db.set_state(name, enabled=int(ok), reason=reason or None,
                              **({} if ok else {"status": "DISABLED", "next_poll": None}))
            if ok:
                self.tasks.append(asyncio.create_task(self._loop(a, stagger=i * 1.5)))

    async def run_once(self, name: str) -> dict:
        a = self.adapters[name]
        if name not in self.locks:
            self.locks[name] = asyncio.Lock()
        async with self.locks[name]:
            self.db.set_state(name, last_attempt=utcnow_iso(), status="POLLING")
            res = await a.poll()
            # ingest is CPU/IO bound (big backfills): keep the event loop free for SSE
            summary = await asyncio.to_thread(self.pipeline.ingest, name, res)
            for k, v in res.kv.items():
                self.db.kv_set(k, v)
            notes = "; ".join(res.notes[:6])[:600] or None
            self.db.set_state(name, status="DEGRADED" if res.partial_errors else "OK",
                              last_success=utcnow_iso(), consecutive_failures=0,
                              last_error=None if not res.partial_errors else notes,
                              last_count=summary["new"], notes=f"{summary}" + (f" | {notes}" if notes else ""))
            return summary

    async def _loop(self, a, stagger: float) -> None:
        # After a restart, honour the schedule: don't re-poll a feed that succeeded < interval ago.
        st = self.db.states().get(a.name, {})
        wait = stagger
        if st.get("last_success"):
            last = datetime.strptime(st["last_success"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            remaining = (last + timedelta(seconds=a.interval) - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                wait = max(stagger, remaining)
                prev = st.get("status")
                self.db.set_state(a.name, status=prev if prev in ("OK", "DEGRADED", "FAILING", "BUDGET") else "OK",
                                  next_poll=_iso(datetime.now(timezone.utc) + timedelta(seconds=wait)))
        await asyncio.sleep(wait)
        failures = 0
        while True:
            delay = a.interval
            try:
                summary = await self.run_once(a.name)
                failures = 0
                log.info("%s ok %s", a.name, summary)
            except asyncio.CancelledError:
                raise
            except BudgetExhausted as exc:
                tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=1, second=0)
                delay = (tomorrow - datetime.now(timezone.utc)).total_seconds()
                self.db.set_state(a.name, status="BUDGET", last_error=str(exc))
            except Exception as exc:  # noqa: BLE001 - every failure is recorded, never swallowed silently
                failures += 1
                delay = min(a.interval, 30 * (2 ** min(failures, 8)))
                self.db.set_state(a.name, status="FAILING", consecutive_failures=failures,
                                  last_error=f"{type(exc).__name__}: {exc}"[:600])
                log.warning("%s failed (%d): %s", a.name, failures, exc)
            nxt = datetime.now(timezone.utc) + timedelta(seconds=delay)
            self.db.set_state(a.name, next_poll=_iso(nxt))
            BUS.publish("health", self.health())
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for a in self.adapters.values():
            await a.http.close()
