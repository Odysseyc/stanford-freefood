"""cardinalengage.stanford.edu -- Stanford's CampusGroups instance.

The /events page is a JS shell behind Sign In and /api/v1/events returns 401,
but the mobile web service used by their own app answers unauthenticated:

    /mobile_ws/v17/mobile_events_list?nb=500

It returns rows in a positional format -- each row has a "fields" key holding a
CSV of column names, and the values live in p0, p1, p2... That listing has the
club name but no description, so for each event we follow the detail page,
which embeds a schema.org Event JSON-LD block with real ISO timestamps. That
block is why there is no date-string parsing anywhere in this file.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re

from .. import config
from ..http import get
from ..models import RawEvent
from ..store import Store

log = logging.getLogger(__name__)

LIST_URL = f"{config.CAMPUSGROUPS_BASE}/mobile_ws/v17/mobile_events_list"
DETAIL_URL = f"{config.CAMPUSGROUPS_BASE}/rsvp_boot"
DETAIL_TTL = 6 * 3600

_LD = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
_TAGS = re.compile(r"<[^>]+>")
# CampusGroups shows this instead of the room when you aren't signed in.
_PRIVATE = re.compile(r"private location|sign in to display", re.I)


def _text(s: str | None) -> str:
    """CampusGroups embeds HTML in JSON string fields. Flatten it."""
    if not s:
        return ""
    return html.unescape(_TAGS.sub(" ", s)).replace("\xa0", " ").strip()


def _decode_rows(rows: list[dict]) -> list[dict]:
    """Turn the positional {fields, p0, p1...} rows into plain dicts."""
    out = []
    for row in rows:
        fields = row.get("fields") or ""
        if "eventId" not in fields:
            continue  # date separators and layout rows
        keys = [k for k in fields.split(",") if k]
        out.append({k: row.get(f"p{i}") for i, k in enumerate(keys)})
    return out


def _parse_detail(body: str) -> dict:
    """Pull the schema.org Event block out of a detail page."""
    for block in _LD.findall(body):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Event":
            return data
    return {}


def fetch(store: Store, limit: int = 500) -> list[RawEvent]:
    resp = get(LIST_URL, params={"nb": limit})
    resp.raise_for_status()
    listing = _decode_rows(resp.json())
    log.info("cardinalengage: %d events listed", len(listing))

    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(days=config.LOOKAHEAD_DAYS)
    out: list[RawEvent] = []

    for row in listing:
        event_id = row.get("eventId")
        if not event_id:
            continue
        url = f"{DETAIL_URL}?id={event_id}"

        body = store.get_http(url, DETAIL_TTL)
        if body is None:
            try:
                r = get(url)
                r.raise_for_status()
                body = r.text
                store.put_http(url, body)
            except Exception as exc:
                log.warning("cardinalengage: detail %s failed: %s", event_id, exc)
                continue

        ld = _parse_detail(body)
        if not ld.get("startDate"):
            continue
        try:
            start = dt.datetime.fromisoformat(ld["startDate"])
        except ValueError:
            continue
        if not (now - dt.timedelta(hours=6) <= start <= horizon):
            continue

        end = None
        if ld.get("endDate"):
            try:
                end = dt.datetime.fromisoformat(ld["endDate"])
            except ValueError:
                pass

        place = _text((ld.get("location") or {}).get("name")) or _text(row.get("eventLocation"))
        if _PRIVATE.search(place or ""):
            place = ""  # classifier will try to recover a room from the prose

        out.append(
            RawEvent(
                source="cardinalengage",
                source_id=str(event_id),
                title=_text(ld.get("name")) or _text(row.get("eventName")) or "(untitled)",
                description=_text(ld.get("description")),
                org=_text(row.get("clubName")) or None,
                location=place or None,
                start=start,
                end=end,
                url=f"{config.CAMPUSGROUPS_BASE}/rsvp?id={event_id}",
            )
        )

    log.info("cardinalengage: %d events in window", len(out))
    return out
