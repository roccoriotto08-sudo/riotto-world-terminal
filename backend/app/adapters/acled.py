"""ACLED  (free for registered users; commercial use requires a licence)
-> curated, geocoded political violence and protest events with fatalities.

Auth: ACLED moved to OAuth in 2025 - exchange account email/password for a bearer
token (valid ~24h), then call /api/acled/read. Data typically lags ~1 week.
"""
import time
from datetime import date, timedelta
from typing import Optional

from ..geo import COUNTRIES
from ..httpclient import ProviderError, require
from ..schema import AdapterResult, GeoEvent
from ..settings import SETTINGS
from .base import Adapter

TOKEN_URL = "https://acleddata.com/oauth/token"
READ_URL = "https://acleddata.com/api/acled/read"
VIOLENT = {"Battles", "Explosions/Remote violence", "Violence against civilians"}


def grade(event_type: str, fatalities: int) -> Optional[int]:
    if event_type in VIOLENT:
        if fatalities >= 50:
            return 5
        if fatalities >= 10:
            return 4
        if fatalities >= 1:
            return 3
        return 2
    if event_type == "Riots":
        return 2 if fatalities == 0 else 3
    if event_type == "Strategic developments":
        return 2
    return 1


class AcledAdapter(Adapter):
    name = "ACLED"
    free = True
    requires = ["acled_email", "acled_password"]
    description = "ACLED: curated conflict & protest events with fatalities (weekly updates)"
    signup_url = "https://acleddata.com/register/"

    def enabled(self):
        if SETTINGS.public_mode and not SETTINGS.public_acled_ok:
            return False, "PUBLIC_MODE: ACLED requires a licence for public display (set ACLED_PUBLIC_LICENSED=true if you have one)"
        return super().enabled()

    def __init__(self, db):
        super().__init__(db)
        self.token = None
        self.token_exp = 0.0

    async def _auth(self) -> str:
        if self.token and time.time() < self.token_exp - 300:
            return self.token
        resp = await self.http.request("POST", TOKEN_URL, data={
            "username": SETTINGS.acled_email, "password": SETTINGS.acled_password,
            "grant_type": "password", "client_id": "acled"})
        js = resp.json()
        require("access_token" in js, "ACLED auth: no access_token in response")
        self.token = js["access_token"]
        self.token_exp = time.time() + float(js.get("expires_in", 86400))
        return self.token

    async def poll(self) -> AdapterResult:
        token = await self._auth()
        days = int(self.cfg.get("backfill_days", 365)) if self.first_run() else 21
        since, until = date.today() - timedelta(days=days), date.today()
        res = AdapterResult()
        page = 1
        while True:
            js = await self.http.get_json(READ_URL, headers={"Authorization": f"Bearer {token}"}, params={
                "_format": "json", "event_date": f"{since}|{until}", "event_date_where": "BETWEEN",
                "fatalities": "1", "fatalities_where": ">=",
                "fields": "event_id_cnty|event_date|event_type|sub_event_type|actor1|actor2|country|iso|"
                          "latitude|longitude|fatalities|location|source",
                "limit": 5000, "page": page})
            require(isinstance(js, dict) and isinstance(js.get("data"), list),
                    f"ACLED: unexpected payload {str(js)[:200]}")
            for r in js["data"]:
                fat = int(r.get("fatalities") or 0)
                sev = grade(r.get("event_type", ""), fat)
                if sev is None or sev < 3:
                    continue
                cat = "CONFLICT" if r.get("event_type") in VIOLENT else (
                    "PROTEST" if r.get("event_type") in ("Protests", "Riots") else "COERCION")
                iso3 = COUNTRIES.n3_to_iso3.get(str(int(r["iso"]))) if r.get("iso") else None
                res.events.append(GeoEvent(
                    id=f"ACLED:{r['event_id_cnty']}", category=cat,
                    title=f"{r.get('sub_event_type')}: {r.get('actor1') or ''}"
                          f"{' vs ' + r['actor2'] if r.get('actor2') else ''} — {r.get('location')}, "
                          f"{r.get('country')} ({fat} fatalities)"[:220],
                    lat=float(r["latitude"]), lon=float(r["longitude"]), country=iso3, severity=sev,
                    ts=f"{r['event_date']}T00:00:00Z", source="ACLED",
                    url=None))
            if len(js["data"]) < 5000:
                break
            page += 1
            if page > 20:
                raise ProviderError("ACLED pagination runaway", retryable=False)
        self.mark_backfilled(res)
        return res
