"""Second calendar: career, recruiting, networking and industry events.

Reuses the same scraped events as the free-food feed (events.stanford.edu +
CardinalEngage), so it costs no extra scraping -- only a second, separately
cached classifier pass. Writes docs/career.ics and docs/career.json.

Also loads manual_events.json, which is how you add things no scraper can see:
Handshake-only employer events, WhatsApp/GroupMe/Slack posts, flyers, emails.
Manual events go straight onto the calendar you tag them for, no model call.

Coverage caveat: most employer recruiting events (info sessions, coffee chats)
live on Handshake, which requires a Stanford login and has no public feed. This
file catches the ones that are cross-posted to events.stanford.edu or a club's
CardinalEngage page; the rest go in manual_events.json or come from Handshake's
own calendar export (see README).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event
from pydantic import BaseModel, Field

from . import config
from .models import FoodEvent, RawEvent
from .store import Store

log = logging.getLogger(__name__)

# --- knobs (env-overridable, same convention as config.py) ------------------
CAL_NAME = os.getenv("CAREER_CAL_NAME", "Stanford Career & Recruiting")
CAL_DESC = "Auto-scraped recruiting, networking, industry and career events."
MIN_CONFIDENCE = float(os.getenv("CAREER_MIN_CONFIDENCE", "0.6"))
ALARM_MINUTES = int(os.getenv("CAREER_ALARM_MINUTES", "60"))
MANUAL_PATH = os.getenv("FF_MANUAL_EVENTS", "manual_events.json")
# Bump the version to force every career event to be re-classified (e.g. after
# editing SYSTEM below). Cached verdicts under the old version are ignored.
CACHE_NS = "career:v1:"
# The first run has ~175 uncached events. Firing them all back-to-back trips
# low-tier API rate limits, so cap new calls per run and pace them. Anything
# over the cap is simply classified on the next run (verdicts are cached), so
# the backlog clears within a few runs and steady-state runs need ~no calls.
MAX_NEW_CALLS = int(os.getenv("CAREER_MAX_NEW_CALLS", "60"))
CALL_DELAY = float(os.getenv("CAREER_CALL_DELAY", "1.2"))

# Recall filter. Deliberately loose; the model does the precision work.
PREFILTER = re.compile(
    r"""
    \b careers? \b | \b recruit (ing|er|ers|ment)? \b | \b internships? \b
  | \b networking \b | \b mixer \b | \b coffee \s+ chats? \b
  | \b info (rmation(al)?)? \s* sessions? \b
  | \b hiring \b | \b jobs? \b | \b employers? \b | \b resumes? \b
  | \b cover \s+ letters? \b | \b mock \s+ interviews? \b
  | \b interview (s|ing)? \s+ (prep|skills|workshop|tips) \b
  | \b case \s+ (prep|interviews?|competitions?) \b | \b expo \b
  | \b industry \s+ (night|panel|speakers?|talk|event|mixer|partners?) \b
  | \b fireside \s+ chat \b | \b venture \s+ capital \b | \b startups? \b
  | \b pitch \s+ (night|competition|event|day) \b | \b hackathon \b
  | \b BEAM \b | \b Career \s* Ed (ucation)? \b | \b Handshake \b | \b headshots? \b
  | \b alumni \s+ (panel|mixer|networking|mentor\w*) \b
  | \b professional \s+ development \b | \b fellowships? \b | \b scholarships? \b
  | \b meet \s+ (the \s+)? (team|recruiters?|engineers?|founders?) \b
  | \b entrepreneur (ship|ial)? \b | \b co-?founder \s+ (and|&) \s+ CEO \b
    """,
    re.I | re.X,
)

CATEGORIES = (
    "Recruiting", "Career fair", "Networking", "Workshop", "Industry talk",
    "Fellowship", "Competition",
)


class CareerVerdict(BaseModel):
    is_relevant: bool = Field(
        description="True only if this is a career, recruiting, networking, "
                    "industry, or professional-development opportunity that "
                    "Stanford students can attend."
    )
    confidence: float = Field(description="0.0 to 1.0.")
    category: str = Field(
        description="Exactly one of: " + ", ".join(CATEGORIES) + ". "
                    "Empty string if not relevant."
    )
    company: str = Field(description="Employer, firm, or fund hosting or "
                                     "presenting, e.g. 'Tesla'. Empty if none.")
    audience: str = Field(description="Who can attend if restricted, e.g. "
                                      "'grad students', 'CS majors', 'women in "
                                      "STEM'. Empty string if open to all students.")
    blurb: str = Field(description="One sentence, max 25 words, saying what the "
                                   "event is and why a student would go. No hype.")
    location_hint: str = Field(description="Room or building named in the text, "
                                           "if any. Empty string if none.")


SYSTEM = """You screen Stanford event listings for students who want career \
opportunities. Decide whether each event is worth putting on a student's \
career calendar.

Answer true for:
- Recruiting: company info sessions, coffee chats, "meet the team", employer \
tabling, events where a company is hiring or taking resumes
- Career fair: career fairs, job/internship expos
- Networking: mixers, alumni/industry networking nights, receptions whose \
point is meeting professionals
- Workshop: resume, cover letter, LinkedIn, interview/case prep, job-search \
and internship-search workshops, headshot sessions
- Industry talk: talks, panels or fireside chats by founders, investors, or \
industry practitioners about their work or careers (e.g. entrepreneurship \
speaker series)
- Fellowship: info sessions for fellowships, scholarships, or funded programs
- Competition: hackathons, pitch competitions, case competitions

Answer false for:
- academic lectures, colloquia, seminars, book talks, and art events, even \
if a speaker bio mentions their "career" or that they "founded" something
- staff- or faculty-only events (benefits enrollment, staff HR training, \
faculty research seminars)
- degree-program info sessions (coterms, honors theses, majors) unless they \
are explicitly about careers or jobs
- events that are only social, religious, athletic, or performances

Ambiguous cases get a low confidence rather than a false. Pick the single \
best category."""


@dataclass
class CareerEvent:
    raw: RawEvent
    category: str
    company: str
    audience: str
    blurb: str
    confidence: float


def _prompt(ev: RawEvent, today: dt.date) -> str:
    when = ev.start.strftime("%A %d %B %Y at %I:%M %p") if ev.start else "unknown"
    return (
        f"Today is {today:%A %d %B %Y}.\n\n"
        f"TITLE: {ev.title}\n"
        f"ORGANISER: {ev.org or 'unknown'}\n"
        f"WHEN: {when}\n"
        f"LOCATION FIELD: {ev.location or '(empty)'}\n"
        f"DESCRIPTION:\n{ev.description[:4000] or '(none)'}"
    )


def classify(events: list[RawEvent], store: Store, client_factory,
             dry_run: bool = False) -> list[CareerEvent]:
    """Same contract as classify.classify: raises ClassifierUnavailable if
    too many calls fail, so a broken key can't publish an empty calendar."""
    from .classify import ClassifierUnavailable

    candidates = [e for e in events if PREFILTER.search(e.haystack)]
    log.info("career prefilter: %d/%d events survive", len(candidates), len(events))

    client = None
    today = dt.date.today()
    kept: list[CareerEvent] = []
    hits = misses = failures = deferred = 0

    for ev in candidates:
        key = CACHE_NS + ev.content_hash
        cached = store.get_verdict(key)
        if cached is not None:
            hits += 1
            verdict = CareerVerdict(**cached)
        else:
            if dry_run:
                log.info("[dry-run] would classify (career): %s", ev.title[:70])
                continue
            if misses + failures >= MAX_NEW_CALLS:
                deferred += 1
                continue
            if client is None:
                # Extra retries: the SDK backs off on 429s using retry-after.
                client = client_factory().with_options(max_retries=6)
            elif CALL_DELAY:
                time.sleep(CALL_DELAY)
            try:
                resp = client.messages.parse(
                    model=config.MODEL,
                    max_tokens=512,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": _prompt(ev, today)}],
                    output_format=CareerVerdict,
                )
                verdict = resp.parsed_output
            except Exception as exc:
                failures += 1
                (log.error if failures == 1 else log.warning)(
                    "career classify failed for %r: %s", ev.title[:50], exc
                )
                continue
            misses += 1
            store.put_verdict(key, verdict.model_dump())

        if verdict.is_relevant and verdict.confidence >= MIN_CONFIDENCE:
            if not ev.location and verdict.location_hint:
                ev.location = verdict.location_hint
            cat = verdict.category if verdict.category in CATEGORIES else "Career"
            kept.append(CareerEvent(ev, cat, verdict.company.strip(),
                                    verdict.audience.strip(), verdict.blurb,
                                    verdict.confidence))

    log.info("career classifier: %d kept (%d cached, %d new calls, %d failed, "
             "%d deferred to next run)", len(kept), hits, misses, failures, deferred)
    attempted = misses + failures
    if failures and failures >= max(1, attempted // 2):
        raise ClassifierUnavailable(
            f"{failures} of {attempted} career classification calls failed "
            "(check the API key's credits and rate limits)"
        )
    return kept


# --- manual events ----------------------------------------------------------
def _parse_time(s: str, tz: ZoneInfo) -> dt.datetime:
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=tz)


def load_manual(path: str = MANUAL_PATH) -> tuple[list[FoodEvent], list[CareerEvent]]:
    """Read manual_events.json. A bad entry is skipped with a warning, never
    fatal -- a typo in one event must not take down both calendars."""
    p = Path(path)
    if not p.exists():
        return [], []
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("manual events: %s is not valid JSON (%s); ignoring it", path, exc)
        return [], []

    tz = ZoneInfo(config.FEED_TZ)
    now = dt.datetime.now(dt.timezone.utc)
    food: list[FoodEvent] = []
    career: list[CareerEvent] = []

    for i, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict) or str(it.get("title", "")).startswith("_"):
            continue  # lets you keep commented-out examples in the file
        try:
            start = _parse_time(it["start"], tz)
            end = _parse_time(it["end"], tz) if it.get("end") else None
        except (KeyError, ValueError) as exc:
            log.warning("manual events: entry %d skipped (bad start/end: %s)", i, exc)
            continue
        if (end or start) < now - dt.timedelta(hours=6):
            continue  # past events drop off on their own
        sid = hashlib.sha1(f"{it['title']}|{it['start']}".encode()).hexdigest()[:16]
        raw = RawEvent(
            source="manual", source_id=sid, title=it["title"],
            description=it.get("notes", ""), org=it.get("org") or it.get("company"),
            location=it.get("location") or None, start=start, end=end,
            url=it.get("url") or None, all_day=bool(it.get("all_day")),
        )
        cal = str(it.get("calendar", "career")).lower()
        blurb = it.get("notes", "")
        if cal in ("career", "both"):
            cat = it.get("category", "Recruiting")
            career.append(CareerEvent(raw, cat if cat in CATEGORIES else "Career",
                                      it.get("company", ""), it.get("audience", ""),
                                      blurb, 1.0))
        if cal in ("food", "both"):
            food.append(FoodEvent(raw=raw, food=it.get("food", ""), blurb=blurb,
                                  confidence=1.0))
    log.info("manual events: %d career, %d food", len(career), len(food))
    return food, career


def merge(scraped: list[CareerEvent], manual: list[CareerEvent]) -> list[CareerEvent]:
    """Manual entries win over a scraped copy of the same event."""
    keys = {m.raw.dedupe_key for m in manual}
    return manual + [c for c in scraped if c.raw.dedupe_key not in keys]


# --- output -----------------------------------------------------------------
NO_LOCATION = "Location not listed - check the event page"


def _where(ev: RawEvent) -> str:
    if ev.location:
        return ev.location
    return "Location TBA - check Handshake" if ev.source == "manual" else NO_LOCATION


def _summary(ce: CareerEvent) -> str:
    title = ce.raw.title.strip()
    if len(title) > 60:
        title = title[:57].rstrip() + "..."
    company = ce.company.strip()
    if company and company.lower() not in title.lower():
        title = f"{company} - {title}"
    return f"[{ce.category}] {title}"


def _description(ce: CareerEvent) -> str:
    ev = ce.raw
    lines = [ce.blurb.strip() or ev.title.strip(), ""]
    if ce.company:
        lines.append(f"Company: {ce.company}")
    if ce.audience:
        lines.append(f"Open to: {ce.audience}")
    if ev.org:
        lines.append(f"Hosted by: {ev.org}")
    lines.append(f"Where: {_where(ev)}")
    if ev.url:
        lines.append(f"Details / RSVP: {ev.url}")
    tag = "added manually" if ev.source == "manual" else \
        f"auto-added - source: {ev.source}, confidence {ce.confidence:.2f}"
    lines += ["", f"({tag})"]
    return "\n".join(lines)


def build_ics(events: list[CareerEvent]) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//stanford-freefood//career//EN")
    cal.add("version", "2.0")
    cal.add("method", "PUBLISH")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", CAL_NAME)
    cal.add("x-wr-caldesc", CAL_DESC)
    cal.add("x-wr-timezone", config.FEED_TZ)
    cal.add("name", CAL_NAME)
    cal.add("refresh-interval", dt.timedelta(minutes=config.REFRESH_MINUTES),
            parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", f"PT{config.REFRESH_MINUTES}M")

    now = dt.datetime.now(dt.timezone.utc)
    for ce in sorted(events, key=lambda e: e.raw.start or now):
        ev = ce.raw
        if ev.start is None:
            continue
        item = Event()
        item.add("uid", f"{ev.uid}@stanford-career")
        item.add("dtstamp", now)
        item.add("summary", _summary(ce))
        item.add("description", _description(ce))
        item.add("location", _where(ev))
        if ev.url:
            item.add("url", ev.url)
        if ev.all_day:
            item.add("dtstart", ev.start.date())
            item.add("dtend", (ev.end or ev.start).date() + dt.timedelta(days=1))
        else:
            item.add("dtstart", ev.start.astimezone(dt.timezone.utc))
            end = ev.end or ev.start + dt.timedelta(hours=1)
            item.add("dtend", end.astimezone(dt.timezone.utc))
            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", _summary(ce))
            alarm.add("trigger", dt.timedelta(minutes=-ALARM_MINUTES))
            item.add_component(alarm)
        cal.add_component(item)
    return cal.to_ical()


def build_json(events: list[CareerEvent]) -> dict:
    tz = ZoneInfo(config.FEED_TZ)
    rows = []
    for ce in events:
        ev = ce.raw
        if ev.start is None:
            continue
        start = ev.start.astimezone(tz)
        end = (ev.end or ev.start + dt.timedelta(hours=1)).astimezone(tz)
        rows.append({
            "uid": ev.uid, "title": ev.title.strip(), "category": ce.category,
            "company": ce.company, "audience": ce.audience, "blurb": ce.blurb.strip(),
            "org": (ev.org or "").strip(), "location": (ev.location or "").strip(),
            "start": start.isoformat(), "end": end.isoformat(),
            "day": start.date().isoformat(), "allDay": ev.all_day,
            "url": ev.url or "", "source": ev.source,
            "confidence": round(ce.confidence, 2),
        })
    rows.sort(key=lambda r: r["start"])
    return {
        "schema": 1, "name": CAL_NAME, "timezone": config.FEED_TZ,
        "generated": now_iso(), "count": len(rows), "events": rows,
    }


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write(events: list[CareerEvent], out_dir: str | None = None) -> None:
    out = Path(out_dir or config.OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / "career.ics").write_bytes(build_ics(events))
    (out / "career.json").write_text(
        json.dumps(build_json(events), indent=1, ensure_ascii=False), encoding="utf-8"
    )
    log.info("wrote %s/career.ics (%d events)", out, len(events))
