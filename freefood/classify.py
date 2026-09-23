"""Two-stage free-food classifier.

Stage 1 is a regex that kills ~95% of events for free. Stage 2 asks Haiku about
the survivors. Verdicts are cached by content hash, so on a steady-state run
almost nothing reaches the model at all.

The model is asked for a location_hint as well as a verdict: CampusGroups hides
the room from signed-out requests, but club descriptions very often name it in
prose ("meet in the Old Union courtyard"). Without this, those events would
land on the calendar with no location.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import anthropic
from pydantic import BaseModel, Field

from . import config
from .models import FoodEvent, RawEvent
from .store import Store

log = logging.getLogger(__name__)


class Verdict(BaseModel):
    is_free_food: bool = Field(
        description="True only if attendees can eat at no cost. False for paid "
                    "meals, food-themed talks, fundraisers, or food drives."
    )
    confidence: float = Field(description="0.0 to 1.0.")
    food: str = Field(description="Short phrase for what's served, e.g. 'pizza', "
                                  "'boba and snacks'. Empty string if unstated.")
    blurb: str = Field(description="One sentence, max 25 words, saying what the "
                                   "event is. No marketing language.")
    location_hint: str = Field(description="Room or building named in the text, "
                                           "if any. Empty string if none.")


SYSTEM = """You screen university event listings for one thing: can a student \
walk in and eat for free?

Answer true only when food or drink is provided at no cost to attendees. \
Answer false for:
- events where food is sold, ticketed, or "available for purchase"
- talks *about* food, cooking demos with no tasting, food-insecurity panels
- food drives and donation collections (attendees give food, not get it)
- receptions restricted to a closed group (a named lab, invited speakers only)
- "food for thought" and similar figures of speech

A generic "refreshments will be served" is a yes. Ambiguous cases get a low \
confidence rather than a false. Be strict about the difference between food \
being present and food being free."""


def _build_prompt(ev: RawEvent, today: dt.date) -> str:
    when = ev.start.strftime("%A %d %B %Y at %I:%M %p") if ev.start else "unknown"
    return (
        f"Today is {today:%A %d %B %Y}.\n\n"
        f"TITLE: {ev.title}\n"
        f"ORGANISER: {ev.org or 'unknown'}\n"
        f"WHEN: {when}\n"
        f"LOCATION FIELD: {ev.location or '(empty)'}\n"
        f"DESCRIPTION:\n{ev.description[:4000] or '(none)'}"
    )


class ClassifierUnavailable(RuntimeError):
    """Raised when enough calls fail that the result can't be trusted.

    This matters more than it looks. The feed is rebuilt from scratch every
    run, so silently treating a dead classifier as "found nothing" would
    publish an empty calendar and wipe every real event off the subscriber's
    phone. Better to fail the run and leave the last good feed in place.
    """


def _make_client() -> anthropic.Anthropic:
    # Identity-linked API keys reject every request unless they carry the id of
    # the workspace to act in. Workspace-scoped keys don't need the header, so
    # send it only when configured.
    workspace = os.getenv("ANTHROPIC_WORKSPACE_ID")
    if workspace:
        return anthropic.Anthropic(
            default_headers={"anthropic-workspace-id": workspace}
        )
    return anthropic.Anthropic()


def classify(events: list[RawEvent], store: Store, dry_run: bool = False) -> list[FoodEvent]:
    candidates = [e for e in events if config.PREFILTER.search(e.haystack)]
    log.info("prefilter: %d/%d events survive", len(candidates), len(events))

    client = None
    today = dt.date.today()
    kept: list[FoodEvent] = []
    hits = misses = failures = 0

    for ev in candidates:
        cached = store.get_verdict(ev.content_hash)
        if cached is not None:
            hits += 1
            verdict = Verdict(**cached)
        else:
            if dry_run:
                # Don't spend tokens during --dry-run on uncached events;
                # surface them as unknown so you can still eyeball the funnel.
                log.info("[dry-run] would classify: %s", ev.title[:70])
                continue
            if client is None:
                client = _make_client()
            try:
                resp = client.messages.parse(
                    model=config.MODEL,
                    max_tokens=512,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": _build_prompt(ev, today)}],
                    output_format=Verdict,
                )
                verdict = resp.parsed_output
            except Exception as exc:
                failures += 1
                # Log the first failure at ERROR so the real cause is visible
                # in CI without trawling the whole log.
                (log.error if failures == 1 else log.warning)(
                    "classify failed for %r: %s", ev.title[:50], exc
                )
                continue
            misses += 1
            store.put_verdict(ev.content_hash, verdict.model_dump())

        if verdict.is_free_food and verdict.confidence >= config.MIN_CONFIDENCE:
            if not ev.location and verdict.location_hint:
                ev.location = verdict.location_hint
            kept.append(
                FoodEvent(
                    raw=ev,
                    food=verdict.food,
                    blurb=verdict.blurb,
                    confidence=verdict.confidence,
                )
            )

    log.info(
        "classifier: %d kept (%d cached, %d new calls, %d failed)",
        len(kept), hits, misses, failures,
    )

    # A dead classifier looks exactly like "no free food anywhere" from the
    # feed's point of view, so refuse to publish rather than silently emptying
    # the calendar. Half is the threshold: transient 429s and the odd malformed
    # event are survivable, a broken key or bad model id is not.
    if failures and failures >= max(1, len(candidates) // 2):
        raise ClassifierUnavailable(
            f"{failures} of {len(candidates)} classification calls failed; "
            "refusing to publish a feed built from a broken classifier"
        )

    return kept
