#Requires -Version 5.1
# Every part of the setup that can be automated without a browser.
#
#     cd path\to\freefood
#     powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# Stops with an explanation if a prerequisite is missing. Safe to re-run.

$ErrorActionPreference = 'Stop'
$Repo = 'stanford-freefood'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }

# --- locate gh --------------------------------------------------------------
# winget updates PATH, but a shell opened before the install won't see it. Look
# in the standard install locations rather than making you restart the terminal.
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) {
    foreach ($c in @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\gh.exe",
        "C:\Program Files\GitHub CLI\gh.exe",
        "${env:ProgramFiles(x86)}\GitHub CLI\gh.exe"
    )) {
        if (Test-Path $c) { $gh = $c; break }
    }
}
if (-not $gh) {
    Write-Host "GitHub CLI not found. Install it, then re-run this script:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    winget install --id GitHub.cli"
    Write-Host ""
    exit 1
}
Warn "using $gh"

# --- auth -------------------------------------------------------------------
& $gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Step "logging in to GitHub (opens a browser)"
    & $gh auth login
    if ($LASTEXITCODE -ne 0) { throw "gh auth login failed" }
}

# --- commit -----------------------------------------------------------------
Step "committing"
if (-not (Test-Path .git)) { git init -q }
git add -A
git commit -q -m "stanford free food scraper" 2>$null
if ($LASTEXITCODE -ne 0) { Warn "nothing new to commit" }
git branch -M main

# --- repo -------------------------------------------------------------------
# Public on purpose: GitHub Pages on a private repo needs a paid plan, and
# public repos get unlimited Actions minutes. The API key is an Actions secret,
# never a file, and the scraped data is already public.
Step "creating the repo"
& $gh repo create $Repo --public --source=. --push --description "Auto-updating calendar of Stanford events with free food"
if ($LASTEXITCODE -ne 0) {
    Warn "repo already exists; pushing instead"
    git push -u origin main
}

# --- secret -----------------------------------------------------------------
Step "your Anthropic API key"
Warn "get one at https://console.anthropic.com/settings/keys"
Warn "paste it at the prompt below"
& $gh secret set ANTHROPIC_API_KEY
if ($LASTEXITCODE -ne 0) { throw "failed to set ANTHROPIC_API_KEY" }

# --- pages ------------------------------------------------------------------
Step "enabling GitHub Pages on main:/docs"
& $gh api -X POST "repos/{owner}/$Repo/pages" -f "source[branch]=main" -f "source[path]=/docs" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Warn "already enabled" }

# --- first run --------------------------------------------------------------
Step "triggering the first run"
& $gh workflow run scrape.yml
if ($LASTEXITCODE -ne 0) { Warn "start it by hand from the Actions tab" }

$owner = & $gh repo view --json owner -q .owner.login

Write-Host ""
Write-Host ("-" * 66)
Write-Host "Done. Give it a few minutes, then your feed is at:"
Write-Host ""
Write-Host "    https://$owner.github.io/$Repo/feed.ics" -ForegroundColor Green
Write-Host ""
Write-Host "Subscribe on iPhone:"
Write-Host "    Settings > Calendar > Accounts > Add Account > Other"
Write-Host "    > Add Subscribed Calendar  ->  paste that URL"
Write-Host ""
Write-Host "Leave 'Remove Alarms' OFF -- the 45-minute alarm on each event is"
Write-Host "the part that actually gets you to the food."
Write-Host ""
Write-Host "Then set Settings > Calendar > Accounts > Fetch New Data"
Write-Host "     > Every 15 Minutes."
Write-Host ""
Write-Host "Watch the run:      gh run watch"
Write-Host "See what it found:  gh run view --log | Select-String scraped"
Write-Host ("-" * 66)
