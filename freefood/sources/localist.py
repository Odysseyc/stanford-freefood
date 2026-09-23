"""events.stanford.edu -- a Localist instance with a fully public JSON API.

No auth, no key, no ToS problem. Two passes:

  firehose  -- every public event in the window (~750 per 60 days). Catches
               departmental seminars with "lunch provided", which are a large
               share of actual free food on campus.
  groups    -- Stanford registers ~400 student orgs as Localist "groups" with
               stable ids. Walking them gives us org attribution, which the
               firehose records don't carry.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import re

from .. import config
from ..http import get
from ..models import RawEvent

log = logging.getLogger(__name__)
API = f"{config.LOCALIST_BASE}/api/2"

_TAGS = re.compile(r"<[^>]+>")


def _clean(s: str | None) -> str:
    """Localist stores HTML entities in plain-text fields ('women&#039;s')."""
    if not s:
        return ""
    return html.unescape(_TAGS.sub(" ", s)).replace(" ", " ").strip()


def _pages(path: str, params: dict, cap: int = 40):
    """Yield each page of a Localist collection endpoint."""
    page = 1
    while page <= cap:
        p = dict(params, pp=100, page=page)
        resp = get(f"{API}/{path}", params=p)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        data = resp.json()
        yield data
        meta = data.get("page") or {}
        if page >= int(meta.get("total") or 1):
            return
        page += 1


def fetch_groups() -> dict[int, str]:
    """{group_id: group_name} for every registered student org."""
    groups: dict[int, str] = {}
    for data in _pages("groups", {}):
        for wrapper in data.get("groups", []):
            g = wrapper["group"]
            groups[g["id"]] = _clean(g["name"])
    log.info("localist: %d groups", len(groups))
    return groups


def _to_raw(ev: dict, org: str | None) -> list[RawEvent]:
    """Explode a Localist event into one RawEvent per upcoming occurrence.

    Recurring events carry many instances; each is a separate calendar entry
    and needs its own stable uid, hence the composite source_id.
    """
    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(days=config.LOOKAHEAD_DAYS)

    # location_name is the venue; room_number is often where the detail is.
    place = " ".join(
        filter(None, [
            _clean(ev.get("location_name") or ev.get("location")),
            _clean(ev.get("room_number")),
        ])
    ).strip()

    if not org:
        depts = ev.get("departments") or []
        if depts:
            org = _clean(depts[0].get("name")) or None

    out = []
    for wrapper in ev.get("event_instances") or []:
        inst = wrapper["event_instance"]
        try:
            start = dt.datetime.fromisoformat(inst["start"])
        except (TypeError, ValueError):
            continue
        if not (now - dt.timedelta(hours=6) <= start <= horizon):
            continue
        end = None
        if inst.get("end"):
            try:
                end = dt.datetime.fromisoformat(inst["end"])
            except ValueError:
                pass
        out.append(
            RawEvent(
                source="localist",
                source_id=f"{ev['id']}:{inst['id']}",
                title=_clean(ev.get("title")) or "(untitled)",
                description=_clean(ev.get("description_text")),
                org=org,
                location=place or None,
                start=start,
                end=end,
                url=ev.get("localist_url") or ev.get("url"),
                all_day=bool(inst.get("all_day")),
            )
        )
    return out


def fetch(with_groups: bool = True) -> list[RawEvent]:
    events: dict[str, RawEvent] = {}

    # Pass A: the firehose.
    for data in _pages("events", {"days": config.LOOKAHEAD_DAYS}):
        for wrapper in data.get("events", []):
            for raw in _to_raw(wrapper["event"], None):
                events[raw.source_id] = raw
    log.info("localist: %d occurrences from firehose", len(events))

    # Pass B: per-group, purely to attach org names. Overwrites the firehose
    # copy because an event we can attribute to a named club is more useful.
    if with_groups:
        groups = fetch_groups()
        for gid, name in groups.items():
            try:
                for data in _pages("events", {"days": config.LOOKAHEAD_DAYS, "group_id": gid}, cap=4):
                    for wrapper in data.get("events", []):
                        for raw in _to_raw(wrapper["event"], name):
                            events[raw.source_id] = raw
            except Exception as exc:  # one bad group must not kill the run
                log.warning("localist: group %s (%s) failed: %s", gid, name, exc)
        log.info("localist: %d occurrences after group pass", len(events))

    return list(events.values())
