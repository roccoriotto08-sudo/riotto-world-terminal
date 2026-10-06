"""Live news store: RSS headlines with topic + watchlist-ticker tags.

Only headline, source, publication time and link are kept - never article text.
Topic tags = the feed's default topics + keyword rules in config/news.yaml.
Ticker tags = watchlist company names (case-sensitive) or explicit ticker forms
($XOM, (XOM), NYSE: XOM), so a generic word like "LNG" does not tag Cheniere.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from .settings import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
  id TEXT PRIMARY KEY, title TEXT, title_norm TEXT, url TEXT, source TEXT, feed TEXT,
  published TEXT, fetched TEXT, topics TEXT, tickers TEXT, hot INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_news_title ON news(title_norm);
CREATE INDEX IF NOT EXISTS ix_news_pub ON news(published);
"""

_WS = re.compile(r"\s+")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().replace("<![CDATA[", "").replace("]]>", "").strip()
    try:
        return _iso(parsedate_to_datetime(s))                    # RFC 822 (RSS)
    except (TypeError, ValueError, IndexError):
        pass
    s = re.sub(r"\s+(EST|EDT)$", lambda m: " -0500" if m.group(1) == "EST" else " -0400", s)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000") if "%z" in fmt else s, fmt)
            return _iso(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return None


def _text(el) -> str:
    return _WS.sub(" ", "".join(el.itertext())).strip() if el is not None else ""


def parse_feed(xml: str) -> List[dict]:
    """RSS 2.0 or Atom -> [{title, url, published}]. Raises ValueError on non-XML."""
    xml = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml.lstrip("﻿"), count=1)
    root = ET.fromstring(xml)
    out = []
    for it in root.iter():
        tag = it.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        kids = {c.tag.rsplit("}", 1)[-1]: c for c in it}
        title = _text(kids.get("title"))
        link = kids.get("link")
        url = (link.get("href") or _text(link)) if link is not None else ""
        if not url and kids.get("guid") is not None and _text(kids["guid"]).startswith("http"):
            url = _text(kids["guid"])
        pub = None
        for k in ("pubDate", "published", "updated", "date"):
            if k in kids:
                pub = parse_date(_text(kids[k]))
                if pub:
                    break
        if title and url:
            out.append({"title": title, "url": url.strip(), "published": pub})
    return out


class Tagger:
    def __init__(self):
        cfg = SETTINGS.news
        self.topic_res = {t: re.compile(r"(?<![\w$])(" + "|".join(re.escape(w) for w in words) + r")(?![\w])", re.I)
                          for t, words in cfg.get("topics", {}).items()}
        self.hot_re = re.compile(r"\b(" + "|".join(re.escape(w) for w in cfg.get("hot_words", [])) + r")\b", re.I)
        self.ticker_res = {}
        for h in SETTINGS.watchlist.get("holdings", []):
            t = re.escape(h["ticker"])
            names = "|".join(re.escape(a) for a in h.get("news_aliases", []))
            pat = rf"(\${t}\b|\({t}\)|\b(?:NYSE|NASDAQ|Nasdaq):\s?{t}\b" + (rf"|\b(?:{names})\b" if names else "") + ")"
            self.ticker_res[h["ticker"]] = re.compile(pat)    # case-sensitive on purpose

    def tag(self, title: str, defaults: List[str]) -> dict:
        topics = set(defaults)
        for t, rx in self.topic_res.items():
            if rx.search(title):
                topics.add(t)
        tickers = [t for t, rx in self.ticker_res.items() if rx.search(title)]
        return {"topics": sorted(topics), "tickers": tickers, "hot": bool(self.hot_re.search(title))}


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:160]


class NewsStore:
    def __init__(self, db):
        self.db = db
        with db.lock:
            db.conn.executescript(SCHEMA)

    def insert(self, items: List[dict]) -> List[dict]:
        new = []
        with self.db.lock:
            for it in items:
                cur = self.db.conn.execute(
                    "INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (it["id"], it["title"], norm_title(it["title"]), it["url"], it["source"], it["feed"],
                     it["published"], it["fetched"], json.dumps(it["topics"]), json.dumps(it["tickers"]), int(it["hot"])))
                if cur.rowcount:
                    new.append(it)
        return new

    def prune(self) -> None:
        cut = _iso(datetime.now(timezone.utc) - timedelta(days=SETTINGS.news.get("retention_days", 14)))
        with self.db.lock:
            self.db.conn.execute("DELETE FROM news WHERE published<?", (cut,))

    def query(self, topic: Optional[str] = None, ticker: Optional[str] = None, q: Optional[str] = None,
              source: Optional[str] = None, hours: int = 48, limit: int = 200) -> List[dict]:
        since = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
        sql, args = "SELECT * FROM news WHERE published>=?", [since]
        if topic:
            sql += " AND topics LIKE ?"
            args.append(f'%"{topic}"%')
        if ticker:
            sql += " AND tickers LIKE ?"
            args.append(f'%"{ticker}"%')
        if source:
            sql += " AND source=?"
            args.append(source)
        if q:
            sql += " AND title LIKE ?"
            args.append(f"%{q}%")
        sql += " ORDER BY published DESC LIMIT ?"
        args.append(limit)
        return [shape(r) for r in self.db._q(sql, tuple(args))]


def shape(r) -> dict:
    d = dict(r)
    d.pop("title_norm", None)
    d["topics"] = json.loads(d["topics"] or "[]")
    d["tickers"] = json.loads(d["tickers"] or "[]")
    d["hot"] = bool(d["hot"])
    return d


def make_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


_STORES: Dict[int, NewsStore] = {}


def news_store(db) -> NewsStore:
    if id(db) not in _STORES:
        _STORES[id(db)] = NewsStore(db)
    return _STORES[id(db)]
