"""Pushes a phone notification when the feed gains events.

Deliberately diffs two rendered .ics files rather than hooking into the
pipeline. The feed is the thing you actually subscribe to, so "what changed in
the feed" is the only definition of "new event" that can't drift from what
lands on your phone.

Every run rewrites DTSTAMP on all 22 events, so a plain file diff is always
non-empty and useless as a signal. UIDs are what identify an occurrence, so
this compares UID sets and stays silent when nothing was added.

    python -m freefood.notify OLD.ics NEW.ics

Prints a notification body to stdout, or nothing at all when no UID is new --
which is the normal case seven or eight times a day.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

# RFC 5545 folds long lines with a CRLF and a leading space. Unfold before
# any parsing, or SUMMARY and UID come back truncated at 75 octets.
_FOLD = re.compile(r"\r?\n[ \t]")

MAX_LISTED = 5


def _events(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    text = _FOLD.sub("", path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        fields = dict(re.findall(r"^(UID|SUMMARY|DTSTART|LOCATION):(.*)$", block, re.M))
        uid = fields.get("UID", "").strip()
        if uid:
            out[uid] = fields
    return out


def _when(dtstart: str) -> str:
    """DTSTART is UTC (trailing Z). Show it in local campus time."""
    try:
        stamp = dt.datetime.strptime(dtstart.strip(), "%Y%m%dT%H%M%SZ")
    except ValueError:
        return ""
    # -7 in term (PDT); close enough for a notification line, and avoids a
    # tzdata dependency in the workflow.
    return (stamp - dt.timedelta(hours=7)).strftime("%a %d %b %H:%M")


def _line(fields: dict[str, str]) -> str:
    title = fields.get("SUMMARY", "").replace("[Free food] ", "").strip()
    # icalendar escapes commas and semicolons in text values.
    title = re.sub(r"\\([,;\\])", r"\1", title)
    when = _when(fields.get("DTSTART", ""))
    return f"{when}  {title}" if when else title


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} OLD.ics NEW.ics", file=sys.stderr)
        return 2

    before = _events(Path(argv[1]))
    after = _events(Path(argv[2]))
    added = [after[uid] for uid in after if uid not in before]
    if not added:
        return 0

    added.sort(key=lambda f: f.get("DTSTART", ""))
    lines = [_line(f) for f in added[:MAX_LISTED]]
    if len(added) > MAX_LISTED:
        lines.append(f"...and {len(added) - MAX_LISTED} more")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
