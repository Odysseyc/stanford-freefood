from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime


def _norm(s: str | None) -> str:
    """Lowercase, strip punctuation and runs of whitespace. Used for dedupe."""
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()


@dataclass
class RawEvent:
    """One occurrence of one event from one source, before classification."""

    source: str          # "localist" | "cardinalengage"
    source_id: str       # unique within the source
    title: str
    description: str = ""
    org: str | None = None
    location: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    url: str | None = None
    all_day: bool = False

    @property
    def uid(self) -> str:
        """Stable per-occurrence identity. Becomes the iCal UID."""
        return hashlib.sha1(f"{self.source}:{self.source_id}".encode()).hexdigest()

    @property
    def content_hash(self) -> str:
        """Changes only when text the classifier reads changes.

        This is what lets re-runs skip the model entirely -- an event whose
        wording hasn't changed can't have changed its free-food verdict.
        """
        blob = "\u0000".join([self.title, self.org or "", self.description])
        return hashlib.sha1(blob.encode()).hexdigest()

    @property
    def dedupe_key(self) -> tuple:
        """Collapses the same real-world event seen through both sources.

        Title prefix rather than full title, because the two platforms often
        differ in trailing punctuation, emoji, or a "| Stanford" suffix.
        """
        day = self.start.date().isoformat() if self.start else "?"
        title_prefix = " ".join(_norm(self.title).split()[:6])
        return (day, title_prefix)

    @property
    def haystack(self) -> str:
        """Everything the prefilter and the model get to look at."""
        return "\n".join(filter(None, [self.title, self.org, self.location, self.description]))


@dataclass
class FoodEvent:
    """A RawEvent the classifier accepted, ready to be written to the feed."""

    raw: RawEvent
    food: str
    blurb: str
    confidence: float
