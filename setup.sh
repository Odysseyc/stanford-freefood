#!/usr/bin/env bash
# Every part of the setup that can be automated without a browser. Run once:
#
#     bash setup.sh
#
# It will stop and tell you what to do if a prerequisite is missing.
set -euo pipefail

REPO=stanford-freefood

if ! command -v gh >/dev/null 2>&1; then
  echo "The GitHub CLI isn't installed. Install it, then re-run this script:"
  echo
  echo "    winget install --id GitHub.cli"
  echo
  echo "Open a NEW terminal afterwards so gh lands on your PATH."
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "==> logging in to GitHub (opens a browser)"
  gh auth login
fi

echo "==> committing"
git init -q 2>/dev/null || true
git add -A
git commit -q -m "stanford free food scraper" 2>/dev/null || echo "    (nothing new to commit)"
git branch -M main

echo "==> creating the repo"
# Public, deliberately: GitHub Pages on a private repo needs a paid plan, and
# public repos also get unlimited Actions minutes. Nothing sensitive lives in
# the code -- the API key is an Actions secret, not a file.
gh repo create "$REPO" --public --source=. --push \
  --description "Auto-updating calendar of Stanford events with free food" \
  || { echo "    (repo exists; pushing instead)"; git push -u origin main; }

echo
echo "==> your Anthropic API key"
echo "    Get one at https://console.anthropic.com/settings/keys"
echo "    Paste it below (input is hidden):"
gh secret set ANTHROPIC_API_KEY

echo "==> enabling GitHub Pages on main:/docs"
gh api -X POST "repos/{owner}/$REPO/pages" \
  -f "source[branch]=main" -f "source[path]=/docs" >/dev/null 2>&1 \
  || echo "    (already enabled)"

echo "==> triggering the first run"
gh workflow run scrape.yml || echo "    (start it by hand from the Actions tab)"

OWNER=$(gh repo view --json owner -q .owner.login)
cat <<EOF

------------------------------------------------------------------
Done. Give it a few minutes, then your feed is at:

    https://${OWNER}.github.io/${REPO}/feed.ics

Subscribe on iPhone:
    Settings > Calendar > Accounts > Add Account > Other
    > Add Subscribed Calendar  ->  paste that URL

Leave "Remove Alarms" OFF -- the 45-minute alarm on each event is
the part that actually gets you to the food.

Then set Settings > Calendar > Accounts > Fetch New Data
     > Every 15 Minutes.

Watch the run:   gh run watch
See what it found: gh run view --log | grep "scraped"
------------------------------------------------------------------
EOF
