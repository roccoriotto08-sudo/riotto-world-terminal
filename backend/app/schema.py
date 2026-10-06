"""Normalized schema shared by every adapter.

{entity, metric, value, unit, observation_date, fetched_at, source,
 release_cadence, status: LIVE | DELAYED | STALE | ERROR}

`status` is NOT stored: it is computed at read time from provider health
(see status.py), so a value can never claim to be LIVE after its feed dies.
"""
import math
import re
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Status = Literal["LIVE", "DELAYED", "STALE", "ERROR"]
Cadence = Literal["tick", "intraday", "daily", "weekly", "monthly", "quarterly", "annual", "irregular", "static"]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class Observation(BaseModel):
    entity: str = Field(min_length=1, max_length=40)
    metric: str = Field(min_length=1, max_length=40)
    value: float
    unit: str
    observation_date: str
    fetched_at: str = Field(default_factory=utcnow_iso)
    source: str
    release_cadence: Cadence
    status: Status = "DELAYED"
    # Provenance flags, e.g. INVERTED, DERIVED, UNASSESSED, MANUAL, DEMO, REVISED, ESTIMATE
    flags: List[str] = Field(default_factory=list)
    period_label: Optional[str] = None  # e.g. "2026-07" for monthly reference periods

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v is None or not math.isfinite(v):
            raise ValueError("value must be finite")
        return float(v)

    @field_validator("observation_date")
    @classmethod
    def _iso(cls, v: str) -> str:
        if not _DATE_RE.match(v):
            raise ValueError(f"observation_date not ISO-8601: {v!r}")
        return v


class GeoEvent(BaseModel):
    id: str
    kind: Literal["GEO", "ALERT"] = "GEO"
    category: str
    title: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    country: Optional[str] = None        # ISO3
    severity: int = Field(ge=1, le=5)
    ts: str                              # event time (UTC ISO)
    fetched_at: str = Field(default_factory=utcnow_iso)
    source: str
    url: Optional[str] = None
    commodities: List[str] = Field(default_factory=list)
    readthrough: Optional[dict] = None   # {text, assets:[{asset,direction,p}], basis}
    rule_id: Optional[str] = None
    trigger: Optional[dict] = None       # data points that fired an ALERT
    entity: Optional[str] = None


class AdapterResult(BaseModel):
    observations: List[Observation] = Field(default_factory=list)
    events: List[GeoEvent] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)     # e.g. partial failures / new release detected
    partial_errors: int = 0
    # cursor updates (kv) committed by the scheduler ONLY after a successful ingest, so an
    # interrupted poll can never mark data as fetched that was not stored
    kv: Dict[str, str] = Field(default_factory=dict)
