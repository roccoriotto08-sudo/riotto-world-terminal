"""SQLite store: observations (with history for the time scrubber), events/alerts,
quarantine, revisions, cross-source discrepancies, provider audit log and state."""
import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .schema import GeoEvent, Observation, utcnow_iso

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS observations (
  entity TEXT NOT NULL, metric TEXT NOT NULL, source TEXT NOT NULL,
  observation_date TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
  fetched_at TEXT NOT NULL, release_cadence TEXT NOT NULL,
  flags TEXT NOT NULL DEFAULT '[]', period_label TEXT,
  PRIMARY KEY (entity, metric, source, observation_date)
);
CREATE INDEX IF NOT EXISTS ix_obs_metric_date ON observations(metric, observation_date);
CREATE TABLE IF NOT EXISTS revisions (
  entity TEXT, metric TEXT, source TEXT, observation_date TEXT,
  old_value REAL, new_value REAL, at TEXT
);
CREATE TABLE IF NOT EXISTS quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT, metric TEXT, source TEXT,
  observation_date TEXT, value REAL, unit TEXT, reason TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS provider_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, url TEXT, status INTEGER,
  requested_at TEXT, responded_at TEXT, latency_ms INTEGER,
  provider_date TEXT, last_modified TEXT, bytes INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_plog_provider ON provider_log(provider, id);
CREATE TABLE IF NOT EXISTS provider_state (
  provider TEXT PRIMARY KEY, status TEXT, enabled INTEGER, reason TEXT,
  last_attempt TEXT, last_success TEXT, next_poll TEXT,
  consecutive_failures INTEGER DEFAULT 0, last_error TEXT, last_count INTEGER, notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, kind TEXT, category TEXT, title TEXT, lat REAL, lon REAL,
  country TEXT, severity INTEGER, ts TEXT, fetched_at TEXT, source TEXT, url TEXT,
  commodities TEXT, readthrough TEXT, rule_id TEXT, trigger TEXT, entity TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS discrepancies (
  entity TEXT, metric TEXT, period TEXT, source_a TEXT, value_a REAL,
  source_b TEXT, value_b REAL, pct REAL, at TEXT,
  PRIMARY KEY (entity, metric, period, source_a, source_b)
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def _end_of_day(as_of: Optional[str]) -> Optional[str]:
    # "2026-10-05" must include intraday stamps such as "2026-10-05T14:00:00Z"
    if as_of and len(as_of) == 10:
        return as_of + "T99"
    return as_of


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ obs
    def upsert_observations(self, obs: Iterable[Observation]) -> Tuple[List[Observation], List[Observation]]:
        """Returns (new, revised). New = key not seen before. Revised = value changed."""
        new, revised = [], []
        obs = list(obs)
        # Chunked transactions: a big backfill must not hold the lock for minutes, because
        # request handlers on the event loop share it.
        for i in range(0, len(obs), 2000):
            self._upsert_chunk(obs[i:i + 2000], new, revised)
        return new, revised

    def _upsert_chunk(self, obs, new, revised) -> None:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN")
            try:
                for o in obs:
                    row = cur.execute(
                        "SELECT value FROM observations WHERE entity=? AND metric=? AND source=? AND observation_date=?",
                        (o.entity, o.metric, o.source, o.observation_date)).fetchone()
                    if row is None:
                        cur.execute(
                            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (o.entity, o.metric, o.source, o.observation_date, o.value, o.unit,
                             o.fetched_at, o.release_cadence, json.dumps(o.flags), o.period_label))
                        new.append(o)
                    elif abs(row["value"] - o.value) > 1e-9 * max(1.0, abs(o.value)):
                        flags = sorted(set(o.flags) | {"REVISED"})
                        cur.execute(
                            "UPDATE observations SET value=?, fetched_at=?, flags=? "
                            "WHERE entity=? AND metric=? AND source=? AND observation_date=?",
                            (o.value, o.fetched_at, json.dumps(flags), o.entity, o.metric, o.source,
                             o.observation_date))
                        cur.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
                                    (o.entity, o.metric, o.source, o.observation_date, row["value"],
                                     o.value, utcnow_iso()))
                        revised.append(o)
                    else:
                        cur.execute(
                            "UPDATE observations SET fetched_at=? "
                            "WHERE entity=? AND metric=? AND source=? AND observation_date=?",
                            (o.fetched_at, o.entity, o.metric, o.source, o.observation_date))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def _q(self, sql: str, args: tuple = ()) -> List[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def latest(self, entity: str, metric: str, source: Optional[str] = None,
               as_of: Optional[str] = None) -> Optional[sqlite3.Row]:
        sql = "SELECT * FROM observations WHERE entity=? AND metric=?"
        args: list = [entity, metric]
        if source:
            sql += " AND source=?"
            args.append(source)
        if as_of:
            sql += " AND observation_date<=?"
            args.append(_end_of_day(as_of))
        sql += " ORDER BY observation_date DESC LIMIT 1"
        rows = self._q(sql, tuple(args))
        return rows[0] if rows else None

    def previous(self, entity: str, metric: str, source: str, before: str) -> Optional[sqlite3.Row]:
        rows = self._q("SELECT * FROM observations WHERE entity=? AND metric=? AND source=? "
                       "AND observation_date<? ORDER BY observation_date DESC LIMIT 1",
                       (entity, metric, source, before))
        return rows[0] if rows else None

    def on_or_before(self, entity: str, metric: str, source: str, date: str) -> Optional[sqlite3.Row]:
        rows = self._q("SELECT * FROM observations WHERE entity=? AND metric=? AND source=? "
                       "AND observation_date<=? ORDER BY observation_date DESC LIMIT 1",
                       (entity, metric, source, _end_of_day(date)))
        return rows[0] if rows else None

    def series(self, entity: str, metric: str, source: str, since: Optional[str] = None,
               until: Optional[str] = None, limit: int = 5000) -> List[sqlite3.Row]:
        sql = "SELECT * FROM observations WHERE entity=? AND metric=? AND source=?"
        args: list = [entity, metric, source]
        if since:
            sql += " AND observation_date>=?"
            args.append(since)
        if until:
            sql += " AND observation_date<=?"
            args.append(_end_of_day(until))
        sql += " ORDER BY observation_date DESC LIMIT ?"
        args.append(limit)
        return list(reversed(self._q(sql, tuple(args))))

    def latest_per_entity(self, metric: str, source: Optional[str] = None,
                          as_of: Optional[str] = None) -> List[sqlite3.Row]:
        # SQLite returns the bare columns of the MAX() row.
        sql = "SELECT *, MAX(observation_date) AS _d FROM observations WHERE metric=?"
        args: list = [metric]
        if source:
            sql += " AND source=?"
            args.append(source)
        if as_of:
            sql += " AND observation_date<=?"
            args.append(_end_of_day(as_of))
        sql += " GROUP BY entity, source"
        return self._q(sql, tuple(args))

    def sources_for(self, entity: str, metric: str) -> List[str]:
        return [r["source"] for r in self._q(
            "SELECT DISTINCT source FROM observations WHERE entity=? AND metric=?", (entity, metric))]

    # ----------------------------------------------------------- quarantine
    def quarantine(self, o: Observation, reason: str) -> None:
        with self.lock:
            dup = self.conn.execute(
                "SELECT 1 FROM quarantine WHERE entity=? AND metric=? AND source=? AND observation_date=? AND value=?",
                (o.entity, o.metric, o.source, o.observation_date, o.value)).fetchone()
            if dup:
                return
            self.conn.execute("INSERT INTO quarantine (entity,metric,source,observation_date,value,unit,reason,at) "
                              "VALUES (?,?,?,?,?,?,?,?)",
                              (o.entity, o.metric, o.source, o.observation_date, o.value, o.unit, reason,
                               utcnow_iso()))

    def quarantine_log(self, limit: int = 200) -> List[dict]:
        return [dict(r) for r in self._q("SELECT * FROM quarantine ORDER BY id DESC LIMIT ?", (limit,))]

    # -------------------------------------------------------- discrepancies
    def record_discrepancy(self, entity, metric, period, sa, va, sb, vb, pct) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR REPLACE INTO discrepancies VALUES (?,?,?,?,?,?,?,?,?)",
                (entity, metric, period, sa, va, sb, vb, pct, utcnow_iso()))
            return cur.rowcount > 0

    def discrepancies(self, limit: int = 200) -> List[dict]:
        return [dict(r) for r in self._q("SELECT * FROM discrepancies ORDER BY period DESC, ABS(pct) DESC LIMIT ?",
                                         (limit,))]

    # ------------------------------------------------------------- events
    def insert_events(self, events: Iterable[GeoEvent]) -> List[GeoEvent]:
        inserted = []
        with self.lock:
            for e in events:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e.id, e.kind, e.category, e.title, e.lat, e.lon, e.country, e.severity, e.ts,
                     e.fetched_at, e.source, e.url, json.dumps(e.commodities),
                     json.dumps(e.readthrough) if e.readthrough else None, e.rule_id,
                     json.dumps(e.trigger) if e.trigger else None, e.entity))
                if cur.rowcount:
                    inserted.append(e)
        return inserted

    def events(self, since: Optional[str] = None, until: Optional[str] = None, kind: Optional[str] = None,
               min_severity: int = 1, country: Optional[str] = None, limit: int = 500) -> List[dict]:
        sql = "SELECT * FROM events WHERE severity>=?"
        args: list = [min_severity]
        if since:
            sql += " AND ts>=?"
            args.append(since)
        if until:
            sql += " AND ts<=?"
            args.append(_end_of_day(until))
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if country:
            sql += " AND country=?"
            args.append(country)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self._q(sql, tuple(args)):
            d = dict(r)
            d["commodities"] = json.loads(d["commodities"] or "[]")
            d["readthrough"] = json.loads(d["readthrough"]) if d["readthrough"] else None
            d["trigger"] = json.loads(d["trigger"]) if d["trigger"] else None
            out.append(d)
        return out

    # ------------------------------------------------------ provider audit
    def log_response(self, provider: str, url: str, status: Optional[int], requested_at: str,
                     responded_at: str, latency_ms: int, provider_date: Optional[str],
                     last_modified: Optional[str], nbytes: int, error: Optional[str]) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO provider_log (provider,url,status,requested_at,responded_at,latency_ms,"
                "provider_date,last_modified,bytes,error) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (provider, url, status, requested_at, responded_at, latency_ms, provider_date,
                 last_modified, nbytes, error))

    def audit(self, provider: Optional[str] = None, limit: int = 200) -> List[dict]:
        if provider:
            rows = self._q("SELECT * FROM provider_log WHERE provider=? ORDER BY id DESC LIMIT ?", (provider, limit))
        else:
            rows = self._q("SELECT * FROM provider_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def set_state(self, provider: str, **fields) -> None:
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO provider_state (provider) VALUES (?)", (provider,))
            if fields:
                cols = ", ".join(f"{k}=?" for k in fields)
                self.conn.execute(f"UPDATE provider_state SET {cols} WHERE provider=?",
                                  (*fields.values(), provider))

    def states(self) -> dict:
        return {r["provider"]: dict(r) for r in self._q("SELECT * FROM provider_state")}

    # ------------------------------------------------------------------ kv
    def kv_get(self, key: str) -> Optional[str]:
        rows = self._q("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def kv_set(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))
