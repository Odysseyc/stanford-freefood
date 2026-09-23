"""Central knobs. Everything tunable lives here."""
import os
import re

# --- Sources ---------------------------------------------------------------
LOCALIST_BASE = "https://events.stanford.edu"
CAMPUSGROUPS_BASE = "https://cardinalengage.stanford.edu"

# How far ahead to scrape. Stanford quarters are ~10 weeks; 60d covers a full
# quarter plus finals without pulling in stale far-future recurring junk.
LOOKAHEAD_DAYS = int(os.getenv("FF_LOOKAHEAD_DAYS", "60"))

# Be polite. Neither endpoint is documented, so stay well under anything that
# could look like abuse.
REQUEST_DELAY = float(os.getenv("FF_REQUEST_DELAY", "0.35"))
HTTP_TIMEOUT = 25
USER_AGENT = os.getenv(
    "FF_USER_AGENT",
    "stanford-freefood/1.0 (personal calendar project; contact via GitHub)",
)

# --- Classifier ------------------------------------------------------------
# Haiku is deliberate here: this is high-volume, low-nuance binary
# classification over short text, and it runs on every event every day.
MODEL = os.getenv("FF_MODEL", "claude-haiku-4-5")
MIN_CONFIDENCE = float(os.getenv("FF_MIN_CONFIDENCE", "0.6"))

# Cheap recall filter. Deliberately over-inclusive -- the model does the
# precision work. Anything this misses never reaches the model, so err loose.
PREFILTER = re.compile(
    r"""
    free \s+ (food|pizza|lunch|dinner|breakfast|snacks?|coffee|boba|meal)
  | \b (pizza|donuts?|bagels?|boba|tacos?|burritos?|sandwiches|cookies)\b
  | \b chick-?fil-?a | panda \s+ express | in-?n-?out | dumplings?
  | refreshments | catered | catering | \bpotluck\b
  | (food|lunch|dinner|breakfast|snacks?|refreshments|drinks|meals?)
        \s+ (will \s+ be \s+ )? (provided|served|available)
  | (provided|served) \s*[:.]? \s* (food|lunch|dinner|snacks?)
  | \b (lunch|dinner|breakfast) \s+ (is \s+ )? on \s+ us \b
  | \b(free|complimentary)\b .{0,30} \b(eats|bites|grub|refreshment)
    """,
    re.I | re.X,
)

# --- Feed ------------------------------------------------------------------
CAL_NAME = os.getenv("FF_CAL_NAME", "Stanford Free Food")
CAL_DESC = "Auto-scraped campus events that mention free food."
FEED_TZ = "America/Los_Angeles"
ALARM_MINUTES = int(os.getenv("FF_ALARM_MINUTES", "45"))
REFRESH_MINUTES = int(os.getenv("FF_REFRESH_MINUTES", "15"))

# GitHub Pages branch-deploy only serves from "/" or "/docs", never an
# arbitrary folder -- hence docs/ rather than the more obvious public/.
OUT_DIR = os.getenv("FF_OUT_DIR", "docs")
DB_PATH = os.getenv("FF_DB_PATH", "state/freefood.db")
