"""Ingest pipeline: units -> sanity/quarantine -> store -> cross-check -> alerts -> publish."""
from collections import defaultdict
from typing import List

from .alerts import AlertEngine, annotate_event
from .bus import BUS
from .db import DB
from .schema import AdapterResult, Observation
from .sanity import SanityChecker
from .settings import SETTINGS
from .units import UnknownUnit, normalize


class Pipeline:
    def __init__(self, db: DB):
        self.db = db
        self.sanity = SanityChecker(SETTINGS.metrics.get("bounds", {}))
        self.alerts = AlertEngine(db)
        self.xcheck_pct = float(SETTINGS.main["crosscheck"]["production_pct_threshold"])

    def _normalize(self, obs: List[Observation]) -> List[Observation]:
        out = []
        for o in obs:
            try:
                v, u = normalize(o.value, o.unit)
            except UnknownUnit as exc:
                self.db.quarantine(o, str(exc))
                continue
            out.append(o.model_copy(update={"value": v, "unit": u}))
        return out

    def _sanity(self, obs: List[Observation]) -> List[Observation]:
        groups = defaultdict(list)
        for o in obs:
            groups[(o.entity, o.metric, o.source)].append(o)
        accepted, quarantined = [], 0
        for (ent, met, src), items in groups.items():
            items.sort(key=lambda o: o.observation_date)
            prev_row = self.db.previous(ent, met, src, items[0].observation_date)
            prev = prev_row["value"] if prev_row else None
            rejected = None   # last quarantined value: lets a genuine level shift through once confirmed
            for o in items:
                ok, reason = self.sanity.check(ent, met, o.value, prev)
                if not ok and rejected is not None and self.sanity.check(ent, met, o.value, rejected)[0]:
                    # second consecutive print consistent with the quarantined level -> regime change,
                    # accept from here on (the first print stays in quarantine for audit)
                    ok = True
                    o = o.model_copy(update={"flags": o.flags + ["LEVEL_SHIFT_CONFIRMED"]})
                if ok:
                    accepted.append(o)
                    prev, rejected = o.value, None
                else:
                    self.db.quarantine(o, reason)
                    rejected = o.value
                    quarantined += 1
        self.last_quarantined = quarantined
        return accepted

    def _crosscheck(self, new: List[Observation]) -> int:
        """EIA vs JODI monthly crude production for the same country-month."""
        flagged = 0
        pairs = {"JODI": "EIA", "EIA": "JODI"}
        for o in new:
            if o.metric != "crude_prod" or o.source not in pairs:
                continue
            other = self.db.latest(o.entity, "crude_prod", pairs[o.source], as_of=o.observation_date)
            if not other or other["observation_date"] != o.observation_date:
                continue
            mean = (o.value + other["value"]) / 2
            if mean <= 0:
                continue
            pct = (o.value - other["value"]) / mean * 100
            if abs(pct) >= self.xcheck_pct:
                a, b = sorted([(o.source, o.value), (other["source"], other["value"])])
                self.db.record_discrepancy(o.entity, "crude_prod", o.period_label or o.observation_date[:7],
                                           a[0], a[1], b[0], b[1], round(pct if a[0] == o.source else -pct, 2))
                flagged += 1
        return flagged

    def ingest(self, adapter_name: str, res: AdapterResult) -> dict:
        obs = self._sanity(self._normalize(res.observations))
        new, revised = self.db.upsert_observations(obs)
        flagged = self._crosscheck(new)
        alerts = self.alerts.evaluate(new)
        inserted_alerts = self.db.insert_events(alerts)
        events = [annotate_event(e) for e in res.events]
        inserted_events = self.db.insert_events(events)

        touched = sorted({o.metric for o in new + revised})
        if touched:
            BUS.publish("data", {"adapter": adapter_name, "metrics": touched,
                                 "entities": sorted({o.entity for o in new + revised})[:200]})
        fresh = sorted(inserted_alerts + inserted_events, key=lambda e: e.ts, reverse=True)[:50]
        for e in fresh:
            BUS.publish("event", e.model_dump())
        return {"received": len(res.observations), "accepted": len(obs), "new": len(new),
                "revised": len(revised), "quarantined": self.last_quarantined if res.observations else 0,
                "discrepancies": flagged, "alerts": len(inserted_alerts), "events": len(inserted_events)}
