"""Adapter contract.

To plug in a new provider:
  1. subclass Adapter, set `name`, `free`, `requires` (env vars), `cadence`
  2. implement `async poll() -> AdapterResult` returning normalized Observations / GeoEvents
     in the PROVIDER's native units (pipeline.py normalizes + sanity-checks them)
  3. raise SchemaError when the payload shape is wrong (never return partial guesses)
  4. register it in adapters/__init__.py and add its poll schedule to config/settings.yaml
"""
from typing import List, Tuple

from ..db import DB
from ..httpclient import ProviderClient
from ..schema import AdapterResult
from ..settings import SETTINGS


# SETTINGS attribute -> the .env variable the user must set
ENV_NAMES = {"fred_key": "FRED_API_KEY", "eia_key": "EIA_API_KEY", "acled_email": "ACLED_EMAIL",
             "acled_password": "ACLED_PASSWORD", "aisstream_key": "AISSTREAM_API_KEY"}


class Adapter:
    name: str = "BASE"
    free: bool = True
    requires: List[str] = []       # attribute names on SETTINGS that must be non-empty
    description: str = ""
    signup_url: str = ""
    realtime: bool = False         # True -> healthy data is LIVE, otherwise DELAYED

    def __init__(self, db: DB):
        self.db = db
        self.cfg = SETTINGS.provider(self.name)
        self.interval = int(self.cfg.get("interval_s", 3600))
        self.stale_after = int(self.cfg.get("stale_after_s", self.interval * 3))
        self.http = ProviderClient(self.name, db, self.cfg.get("rate_per_min"), self.cfg.get("daily_limit"))

    def enabled(self) -> Tuple[bool, str]:
        missing = [r for r in self.requires if not getattr(SETTINGS, r)]
        if missing:
            return False, "missing " + ", ".join(ENV_NAMES.get(m, m.upper()) for m in missing)
        return True, ""

    def first_run(self) -> bool:
        return self.db.kv_get(f"{self.name}:backfilled") is None

    def mark_backfilled(self, res: AdapterResult) -> None:
        from ..schema import utcnow_iso
        res.kv[f"{self.name}:backfilled"] = utcnow_iso()

    async def poll(self) -> AdapterResult:  # pragma: no cover - interface
        raise NotImplementedError
