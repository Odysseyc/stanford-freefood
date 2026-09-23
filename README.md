# Stanford Free Food

Scrapes Stanford campus events, keeps the ones with free food, and publishes an
`.ics` feed you subscribe to from your phone.

```
events.stanford.edu (Localist)  ─┐
                                 ├─► dedupe ─► regex ─► Haiku ─► feed.ics ─► Cloudflare Pages
cardinalengage.stanford.edu ─────┘
```

Runs on GitHub Actions every 3 hours. No server, no always-on machine.

## Sources

**`events.stanford.edu`** — a Localist instance with a fully public JSON API.
No auth, no key. Two passes: a firehose (~750 occurrences per 60 days, which is
where "lunch provided" seminars live) and a per-group pass over the ~396
registered student orgs, which is what attaches club names to events.

**`cardinalengage.stanford.edu`** — Stanford's CampusGroups instance. The
`/events` page is behind Sign In and `/api/v1/events` returns 401, but the
mobile web service (`/mobile_ws/v17/mobile_events_list`) answers
unauthenticated. Event detail pages embed schema.org JSON-LD with real ISO
timestamps, so there is no date-string parsing anywhere in the codebase.

Both are read-only, rate-limited to ~3 req/sec, and hit only public endpoints.
Nothing here logs in as you.

## Setup

```bash
winget install --id GitHub.cli   # if you don't have it; open a new terminal after
bash setup.sh
```

`setup.sh` does everything scriptable: commits, creates the repo, prompts once
for your Anthropic API key, enables Pages, and kicks off the first run. It
prints your feed URL at the end.

It needs two things from you that no script can obtain:

- **A GitHub login** — it runs `gh auth login`, which opens a browser.
- **An Anthropic API key** — from
  <https://console.anthropic.com/settings/keys>. Costs a couple of dollars a
  month at most; verdicts are cached by content hash, so on a steady-state run
  almost nothing reaches the model.

The repo is created **public** on purpose. GitHub Pages on a private repo needs
a paid plan, and public repos get unlimited Actions minutes. Nothing sensitive
is in the code — the API key lives in Actions secrets, and the scraped data is
already public. To keep it private instead, see the commented Cloudflare Pages
step at the bottom of `.github/workflows/scrape.yml`.

### Subscribe

On iPhone: **Settings → Calendar → Accounts → Add Account → Other → Add
Subscribed Calendar**, paste the feed URL.

**Leave "Remove Alarms" off.** Every event carries a 45-minute alarm, and that
alarm is what actually gets you to the food — feed refresh is 20–40 minutes
end-to-end no matter what you set, because iOS subscriptions are fetched by
Apple's servers on Apple's schedule.

Then: **Settings → Calendar → Accounts → Fetch New Data → Every 15 Minutes.**

## Local use

```bash
pip install -r requirements.txt

python -m freefood.main --dry-run --no-groups   # fast, spends no tokens
python -m freefood.main                          # full run, writes docs/feed.ics
```

`--dry-run` prints the funnel (scraped → prefiltered → kept) and writes nothing,
so you can check the classifier's precision before it touches your calendar.
`--no-groups` skips the ~396-request Localist org pass, which takes ~3 minutes.

## Tuning

Everything lives in `freefood/config.py`, overridable by env var:

| Var | Default | What it does |
|---|---|---|
| `FF_MIN_CONFIDENCE` | `0.6` | Raise if you get false positives |
| `FF_ALARM_MINUTES` | `45` | Alarm lead time |
| `FF_LOOKAHEAD_DAYS` | `60` | Scrape window |
| `FF_MODEL` | `claude-haiku-4-5` | Classifier model |
| `FF_REQUEST_DELAY` | `0.35` | Seconds between HTTP requests |

The regex prefilter in `config.py` is deliberately over-inclusive — it decides
what the model is even allowed to see, so it errs toward recall. The model does
the precision work, including rejecting the traps: food *drives*, ticketed
dinners, "food for thought", and talks about food.

## Notes

- **Timing.** Stanford quarters start late September. Out of term the feed is
  nearly empty — that's correct behaviour, not a bug.
- **Locations.** CardinalEngage hides rooms from signed-out requests
  (`Private Location (sign in to display)`). When that happens the classifier
  tries to recover a room from the description prose. If it can't, the event
  still ships with a link and an explicit "location not listed" rather than a
  blank field.
- **Scheduled workflow expiry.** GitHub disables cron workflows after 60 days
  of repo inactivity. The run commits `state/`, which counts as activity, so
  this stays alive on its own.
