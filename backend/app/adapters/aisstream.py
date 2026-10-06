"""AISStream.io  (free, key required) -> live terrestrial AIS in the oil regions listed in
config/ais.yaml (Hormuz, Red Sea, Malacca, US Gulf, North Sea, Black Sea, ...).

A websocket session runs for `session_s`, then the scheduler reconnects (with backoff on
failure). Position reports do not carry ship type, so positions are held in memory until
the vessel's ShipStaticData (broadcast every ~6 min) identifies it as a tanker (AIS 80-89).
Writes are batched every 5 s off the event loop.
"""
import asyncio
import json
import time
from datetime import datetime, timezone

import websockets

from ..httpclient import ProviderError
from ..schema import AdapterResult, utcnow_iso
from ..settings import SETTINGS
from ..vessels import eta_str, is_tanker, iso, store_for
from .base import Adapter

URL = "wss://stream.aisstream.io/v0/stream"


def _meta_latlon(meta: dict):
    return (meta.get("Latitude", meta.get("latitude")), meta.get("Longitude", meta.get("longitude")))


class AisStreamAdapter(Adapter):
    name = "AISSTREAM"
    free = True
    realtime = True
    requires = ["aisstream_key"]
    description = "AISStream.io live AIS: tanker positions in the oil regions of config/ais.yaml"
    signup_url = "https://aisstream.io"

    def __init__(self, db):
        super().__init__(db)
        self.types = {}      # mmsi -> AIS ship type (learned from static data, survives sessions)
        self.pending = {}    # mmsi -> latest position of a vessel whose type is not yet known

    async def poll(self) -> AdapterResult:
        store = store_for(self.db)
        for m in store.known_tankers():
            self.types.setdefault(m, 80)
        sub = {"APIKey": SETTINGS.aisstream_key,
               "BoundingBoxes": [b["box"] for b in SETTINGS.ais["aisstream_boxes"]],
               "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}
        session = float(self.cfg.get("session_s", 900))
        counts = {"msgs": 0, "static": 0, "pos": 0}
        static_buf, pos_buf = [], []
        t_end, t_flush = time.monotonic() + session, time.monotonic() + 5
        try:
            async with websockets.connect(URL, compression="deflate", open_timeout=15, ping_interval=20,
                                          max_size=2 ** 22) as ws:
                await ws.send(json.dumps(sub))      # must arrive within 3 s of connecting
                while time.monotonic() < t_end:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        raise ProviderError("AISStream: no messages for 30 s")
                    msg = json.loads(raw)
                    if "error" in msg:
                        raise ProviderError(f"AISStream: {msg['error']}", retryable=False)
                    counts["msgs"] += 1
                    self._handle(msg, static_buf, pos_buf)
                    if time.monotonic() >= t_flush:
                        await self._flush(store, static_buf, pos_buf, counts)
                        static_buf, pos_buf = [], []
                        t_flush = time.monotonic() + 5
                await self._flush(store, static_buf, pos_buf, counts)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            raise ProviderError(f"AISStream connection: {exc}")
        if counts["msgs"] == 0:
            raise ProviderError("AISStream: session produced no messages (check key / subscription)")
        return AdapterResult(notes=[f"session: {counts['msgs']} msgs, {counts['static']} tanker static, "
                                    f"{counts['pos']} tanker positions, {len(self.types)} types known"])

    def _handle(self, msg: dict, static_buf: list, pos_buf: list) -> None:
        kind, meta, body = msg.get("MessageType"), msg.get("MetaData") or {}, msg.get("Message") or {}
        mmsi = meta.get("MMSI")
        if not mmsi:
            return
        now = datetime.now(timezone.utc)
        if kind == "ShipStaticData":
            s = body.get("ShipStaticData") or {}
            t = s.get("Type")
            self.types[mmsi] = t
            if is_tanker(t):
                dim, eta = s.get("Dimension") or {}, s.get("Eta") or {}
                static_buf.append({"mmsi": mmsi, "name": (s.get("Name") or meta.get("ShipName") or "").strip() or None,
                                   "imo": s.get("ImoNumber") or None, "callsign": (s.get("CallSign") or "").strip() or None,
                                   "ship_type": t, "length": (dim.get("A", 0) + dim.get("B", 0)) or None,
                                   "beam": (dim.get("C", 0) + dim.get("D", 0)) or None,
                                   "draught": s.get("MaximumStaticDraught") or None,
                                   "destination": (s.get("Destination") or "").strip() or None,
                                   "eta": eta_str(eta.get("Month", 0), eta.get("Day", 0), eta.get("Hour", 24), eta.get("Minute", 60), now),
                                   "ts": iso(now), "source": "AISSTREAM"})
                if mmsi in self.pending:
                    pos_buf.append(self.pending.pop(mmsi))
        elif kind == "PositionReport":
            p = body.get("PositionReport") or {}
            lat, lon = p.get("Latitude"), p.get("Longitude")
            if lat is None:
                lat, lon = _meta_latlon(meta)
            row = {"mmsi": mmsi, "lat": lat, "lon": lon, "sog": p.get("Sog"), "cog": p.get("Cog"),
                   "heading": p.get("TrueHeading"), "nav_status": p.get("NavigationalStatus"),
                   "ts": iso(now), "source": "AISSTREAM"}      # receive time (stream is real-time)
            t = self.types.get(mmsi)
            if t is None:
                self.pending[mmsi] = row
                if len(self.pending) > 200_000:            # bound memory: drop the oldest half
                    for k in list(self.pending)[:100_000]:
                        self.pending.pop(k, None)
            elif is_tanker(t):
                pos_buf.append(row)

    async def _flush(self, store, static_buf, pos_buf, counts) -> None:
        if static_buf:
            counts["static"] += await asyncio.to_thread(store.upsert_static, static_buf)
        if pos_buf:
            counts["pos"] += await asyncio.to_thread(store.upsert_positions, pos_buf)
        self.db.set_state(self.name, status="STREAMING", last_success=utcnow_iso(),
                          notes=f"live: {counts['msgs']} msgs this session, {counts['pos']} tanker positions")
