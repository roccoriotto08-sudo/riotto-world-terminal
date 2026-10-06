"""Live news from publishers' RSS feeds (free, no key). See config/news.yaml.

Each feed is fetched independently; a dead feed is a partial error (the rest still
update). Items without a parseable publication time, or dated in the future by more
than 10 minutes, are dropped rather than given an invented time.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from ..bus import BUS
from ..httpclient import ProviderError
from ..news import Tagger, make_id, news_store, parse_feed
from ..schema import AdapterResult, utcnow_iso
from ..settings import SETTINGS
from .base import Adapter

UA = {"User-Agent": "Mozilla/5.0 (compatible; riotto-world-terminal/0.1; RSS reader)",
      "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}


class NewsAdapter(Adapter):
    name = "NEWS"
    free = True
    realtime = True
    description = "Live headlines (RSS): war/geopolitics, oil & energy, shipping, US equities, macro"
    signup_url = "config/news.yaml (publisher RSS, no key)"

    def __init__(self, db):
        super().__init__(db)
        self.tagger = Tagger()

    async def poll(self) -> AdapterResult:
        store = news_store(self.db)
        res = AdapterResult()
        now = datetime.now(timezone.utc)
        future_cut = (now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        items, ok = [], 0
        for f in SETTINGS.news.get("feeds", []):
            try:
                xml = await self.http.get_text(f["url"], headers=UA)
                parsed = parse_feed(xml)
            except (ProviderError, ValueError, SyntaxError) as exc:
                res.partial_errors += 1
                res.notes.append(f"{f['id']}: {str(exc)[:80]}")
                continue
            ok += 1
            for p in parsed:
                if not p["published"] or p["published"] > future_cut:
                    continue
                tags = self.tagger.tag(p["title"], f.get("topics", []))
                items.append({"id": make_id(p["url"]), "title": p["title"][:300], "url": p["url"], "source": f["name"],
                              "feed": f["id"], "published": p["published"], "fetched": utcnow_iso(), **tags})
        if ok == 0:
            raise ProviderError("no news feed reachable: " + "; ".join(res.notes[:3]))
        new = await asyncio.to_thread(store.insert, items)
        await asyncio.to_thread(store.prune)
        fresh = sorted(new, key=lambda x: x["published"], reverse=True)
        first_run = self.first_run()
        if fresh and not first_run:
            BUS.publish("news", fresh[:30])          # live push (skipped for the initial backfill burst)
        self.mark_backfilled(res)
        res.notes.insert(0, f"{ok}/{len(SETTINGS.news.get('feeds', []))} feeds, {len(new)} new headlines")
        return res
