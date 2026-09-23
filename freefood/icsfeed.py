"""Renders classified events to an iCalendar feed.

Two deliberate choices here:

  VALARM  -- an alarm 45 minutes out is what actually gets you to the food.
             Feed refresh latency (GitHub Actions cron + CDN + Apple's poll)
             is 20-40 minutes end to end and can't be engineered below that,
             but it only affects how fast *newly posted* events appear. Clubs
             post hours to days ahead, so the alarm is the part that matters.

  REFRESH-INTERVAL / X-PUBLISHED-TTL -- hints, not guarantees. Apple and
             Google may ignore them, but they cost one line each.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from icalendar import Alarm, Calendar, Event

from . import config
from .models import FoodEvent

log = logging.getLogger(__name__)

NO_LOCATION = "Location not listed - check the event page"


def _summary(fe: FoodEvent) -> str:
    title = fe.raw.title.strip()
    if len(title) > 60:
        title = title[:57].rstrip() + "..."
    food = (fe.food or "").strip()
    return f"[Free food] {food} - {title}" if food else f"[Free food] {title}"


def _description(fe: FoodEvent) -> str:
    ev = fe.raw
    lines = [fe.blurb.strip() or ev.title.strip(), ""]
    if fe.food:
        lines.append(f"Food: {fe.food}")
    if ev.org:
        lines.append(f"Hosted by: {ev.org}")
    lines.append(f"Where: {ev.location or NO_LOCATION}")
    if ev.url:
        lines.append(f"Details: {ev.url}")
    lines += [
        "",
        f"(auto-added - source: {ev.source}, confidence {fe.confidence:.2f})",
    ]
    return "\n".join(lines)


def build(events: list[FoodEvent]) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//stanford-freefood//EN")
    cal.add("version", "2.0")
    cal.add("method", "PUBLISH")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", config.CAL_NAME)
    cal.add("x-wr-caldesc", config.CAL_DESC)
    cal.add("x-wr-timezone", config.FEED_TZ)
    cal.add("name", config.CAL_NAME)
    refresh = dt.timedelta(minutes=config.REFRESH_MINUTES)
    cal.add("refresh-interval", refresh, parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", f"PT{config.REFRESH_MINUTES}M")

    now = dt.datetime.now(dt.timezone.utc)

    for fe in sorted(events, key=lambda e: e.raw.start or now):
        ev = fe.raw
        if ev.start is None:
            continue

        item = Event()
        item.add("uid", f"{ev.uid}@stanford-freefood")
        item.add("dtstamp", now)
        item.add("summary", _summary(fe))
        item.add("description", _description(fe))
        # LOCATION is never blank -- an event you can't find is useless.
        item.add("location", ev.location or NO_LOCATION)
        if ev.url:
            item.add("url", ev.url)

        if ev.all_day:
            item.add("dtstart", ev.start.date())
            item.add("dtend", (ev.end or ev.start).date() + dt.timedelta(days=1))
        else:
            start = ev.start.astimezone(dt.timezone.utc)
            end = (ev.end or ev.start + dt.timedelta(hours=1)).astimezone(dt.timezone.utc)
            item.add("dtstart", start)
            item.add("dtend", end)

            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", _summary(fe))
            alarm.add("trigger", dt.timedelta(minutes=-config.ALARM_MINUTES))
            item.add_component(alarm)

        cal.add_component(item)

    return cal.to_ical()


def write(events: list[FoodEvent], out_dir: str = None) -> Path:
    out = Path(out_dir or config.OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "feed.ics"
    path.write_bytes(build(events))
    log.info("wrote %s (%d events, %d bytes)", path, len(events), path.stat().st_size)
    return path
