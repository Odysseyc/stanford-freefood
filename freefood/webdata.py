"""Renders classified events to JSON for the website.

The .ics feed and this file carry the same events, but they answer different
questions. A calendar client wants one VEVENT per occurrence with an alarm
attached; the website wants to group by day, filter by host, and show what the
food actually is -- all of which means keeping the fields the .ics flattens
into a prose DESCRIPTION.

Written on every run alongside feed.ics, and served as a static file. The page
fetches it directly, so there is no API and nothing to keep running.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .models import FoodEvent

log = logging.getLogger(__name__)

SCHEMA = 1


def _event(fe: FoodEvent, tz: ZoneInfo) -> dict:
    ev = fe.raw
    # Local time, not UTC: every consumer of this file is standing on campus,
    # and shipping UTC would push tz conversion into the page for no gain.
    start = ev.start.astimezone(tz)
    end = (ev.end or ev.start + dt.timedelta(hours=1)).astimezone(tz)
    return {
        "uid": ev.uid,
        "title": ev.title.strip(),
        "food": (fe.food or "").strip(),
        "blurb": fe.blurb.strip(),
        "org": (ev.org or "").strip(),
        "location": (ev.location or "").strip(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "day": start.date().isoformat(),
        "allDay": ev.all_day,
        "url": ev.url or "",
        "source": ev.source,
        "confidence": round(fe.confidence, 2),
    }


def build(events: list[FoodEvent]) -> dict:
    tz = ZoneInfo(config.FEED_TZ)
    rows = [_event(fe, tz) for fe in events if fe.raw.start is not None]
    rows.sort(key=lambda r: r["start"])
    return {
        "schema": SCHEMA,
        "name": config.CAL_NAME,
        "timezone": config.FEED_TZ,
        # UTC here on purpose -- this one is a machine timestamp the page
        # renders as "updated 2 hours ago", not a wall clock anyone reads.
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(rows),
        "events": rows,
    }


def write(events: list[FoodEvent], out_dir: str = None) -> Path:
    out = Path(out_dir or config.OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "events.json"
    payload = build(events)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s (%d events, %d bytes)", path, payload["count"], path.stat().st_size)
    return path
