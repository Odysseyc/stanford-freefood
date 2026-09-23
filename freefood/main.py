from __future__ import annotations

import argparse
import logging
import os
import sys

from . import career, config, icsfeed, webdata
from .classify import ClassifierUnavailable, _make_client, classify
from .models import RawEvent
from .sources import cardinalengage, localist
from .store import Store

log = logging.getLogger("freefood")


def dedupe(events: list[RawEvent]) -> list[RawEvent]:
    """Collapse the same event seen through both platforms.

    CardinalEngage wins ties: it carries the club name, which Localist's
    firehose records don't, and club attribution is most of what makes the
    calendar readable.
    """
    best: dict[tuple, RawEvent] = {}
    for ev in events:
        key = ev.dedupe_key
        cur = best.get(key)
        if cur is None:
            best[key] = ev
            continue
        score = (ev.source == "cardinalengage", len(ev.description))
        cur_score = (cur.source == "cardinalengage", len(cur.description))
        if score > cur_score:
            best[key] = ev
    dropped = len(events) - len(best)
    if dropped:
        log.info("dedupe: collapsed %d duplicate occurrences", dropped)
    return list(best.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="freefood")
    ap.add_argument("--dry-run", action="store_true",
                    help="Scrape and filter, print the funnel, write nothing and "
                         "spend no tokens on uncached events.")
    ap.add_argument("--no-groups", action="store_true",
                    help="Skip the ~400-request Localist per-group pass.")
    ap.add_argument("--no-campusgroups", action="store_true")
    ap.add_argument("--no-career", action="store_true",
                    help="Skip the career/recruiting calendar.")
    ap.add_argument("--out", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    # Fail fast and legibly rather than scraping for two minutes and then
    # dying inside the classifier with an SDK traceback.
    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        log.error(
            "ANTHROPIC_API_KEY is not set. In CI: gh secret set ANTHROPIC_API_KEY. "
            "Locally: $env:ANTHROPIC_API_KEY='sk-ant-...'. "
            "Use --dry-run to scrape without classifying."
        )
        return 2

    store = Store(config.DB_PATH)
    store.vacuum_old()

    raw: list[RawEvent] = []
    try:
        raw += localist.fetch(with_groups=not args.no_groups)
    except Exception as exc:
        log.error("localist source failed entirely: %s", exc)

    if not args.no_campusgroups:
        try:
            raw += cardinalengage.fetch(store)
        except Exception as exc:
            log.error("cardinalengage source failed entirely: %s", exc)

    if not raw:
        log.error("no events from any source; refusing to write an empty feed")
        return 1

    raw = dedupe(raw)
    try:
        found = classify(raw, store, dry_run=args.dry_run)
    except ClassifierUnavailable as exc:
        log.error("%s", exc)
        log.error("leaving the existing feed untouched")
        return 3

    manual_food, manual_career = career.load_manual()
    found += [m for m in manual_food
              if m.raw.dedupe_key not in {f.raw.dedupe_key for f in found}]

    # Career feed. A failure here must never block the food feed, so it is
    # handled separately: on failure the old career.ics is simply left alone.
    career_events = None
    if not args.no_career:
        try:
            scraped = career.classify(raw, store, _make_client, dry_run=args.dry_run)
            career_events = career.merge(scraped, manual_career)
        except ClassifierUnavailable as exc:
            log.error("%s -- leaving the existing career feed untouched", exc)

    print(f"\n{'=' * 72}")
    print(f"scraped {len(raw)} events -> {len(found)} with free food")
    print("=" * 72)
    for fe in sorted(found, key=lambda e: e.raw.start):
        when = fe.raw.start.strftime("%a %d %b %H:%M")
        print(f"{when}  {fe.food or 'food':<18.18}  {fe.raw.title[:44]:<44.44}  "
              f"{(fe.raw.location or '-')[:28]}")
    print()

    if career_events is not None:
        print(f"{len(career_events)} career / recruiting events")
        print("=" * 72)
        for ce in sorted(career_events, key=lambda e: e.raw.start):
            when = ce.raw.start.strftime("%a %d %b %H:%M")
            print(f"{when}  {ce.category:<13.13}  {(ce.company or '-'):<12.12}  "
                  f"{ce.raw.title[:46]}")
        print()

    if args.dry_run:
        log.info("dry run: not writing feed")
        return 0

    icsfeed.write(found, args.out)
    webdata.write(found, args.out)
    if career_events is not None:
        career.write(career_events, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
