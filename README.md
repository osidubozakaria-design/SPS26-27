# SPS 26/27 — API-Football automatic data sync

This starter connects the SPS 26/27 public site to API-Football without exposing the API key in the browser.

## Architecture

API-Football → GitHub Actions → players.json → GitHub Pages → SPS site

The public `index.html` already reads `./players.json`, so the site can keep using the same public frontend.

## 1. Create your API-Football account

Use the official API-Football dashboard and create a free API key.

The current free plan is 100 requests/day with a 10 requests/minute limit. It provides access to the available endpoints and competitions, subject to the plan's season/history limitations.

## 2. Add the API key to GitHub

In your `SPS26-27` repository:

Settings → Secrets and variables → Actions → New repository secret

Name:
`API_FOOTBALL_KEY`

Value:
your API-Football key

Never put the key inside `index.html`, `players.json`, or any public JavaScript file.

## 3. Upload these files

Copy these into the root of the repository:

- `sync.py`
- `config.json`
- `requirements.txt`
- `.github/workflows/sps-sync.yml`
- `data/`
- your existing `players.json`

Do not replace your existing public `index.html` with the sync script.

## 4. Run it manually first

GitHub:
Actions → SPS automatic data sync → Run workflow.

Open the workflow log and confirm:
- API quota is reported
- players are mapped
- completed fixtures are found
- `players.json` is updated

## Important first-version limitation

This starter intentionally automates objective match statistics first.

Your SPS awards such as Golden Boot, Assist Leader, MVP and Final MVP are preserved as manual fields. They should not be overwritten by the automatic sync until we add competition-aware award logic.

Also, the first version uses player-name matching. Before calling this production-ready, we should add permanent API player IDs and club/team mapping for each SPS record. This avoids problems when players share names or transfer clubs.

## Quota strategy

The workflow is deliberately conservative. It runs twice per day and caches API responses.

Do not poll every 30 seconds from the public website. That would exhaust the free quota very quickly.

For genuinely live match updates, move polling to a server/backend or upgrade the API plan.

## Next upgrade

After this starter works, add:
1. permanent API player IDs
2. competition-specific aggregation
3. league position points
4. cup stage points
5. automatic Golden Boot / Assist Leader calculation
6. automatic SPS score calculation
7. automatic live-match dashboard
8. admin-only award overrides
