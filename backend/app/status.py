"""Read-time status: LIVE | DELAYED | STALE | ERROR, derived from provider health.

  LIVE     realtime feed (market quotes) polled successfully within stale_after
  DELAYED  feed healthy, but data is end-of-day / periodic by nature (FRED, EIA, JODI...)
  STALE    the provider has not succeeded within stale_after -> showing last good value
  ERROR    no value has ever been obtained
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

SOURCE_TO_PROVIDER = {"EIA_STEO": "EIA"}


def parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    ts = ts.replace("Z", "+00:00")
    if len(ts) == 10:
        ts += "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_seconds(ts: Optional[str]) -> Optional[int]:
    dt = parse_ts(ts)
    return int((datetime.now(timezone.utc) - dt).total_seconds()) if dt else None


class StatusResolver:
    def __init__(self, adapters: Dict[str, object], states_fn):
        self.adapters = adapters
        self.states_fn = states_fn

    def provider_age(self, source: str) -> Tuple[Optional[int], Optional[int], bool]:
        prov = SOURCE_TO_PROVIDER.get(source, source)
        st = self.states_fn().get(prov, {})
        ad = self.adapters.get(prov)
        stale_after = getattr(ad, "stale_after", 86400)
        realtime = getattr(ad, "realtime", False)
        return age_seconds(st.get("last_success")), stale_after, realtime

    def status(self, source: Optional[str], cadence: Optional[str]) -> Tuple[str, Optional[int]]:
        """Returns (status, seconds since the provider last succeeded)."""
        if not source:
            return "ERROR", None
        age, stale_after, realtime = self.provider_age(source)
        if age is None or age > stale_after:
            return "STALE", age
        if realtime and cadence in ("tick", "intraday"):
            return "LIVE", age
        return "DELAYED", age
