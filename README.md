# SPS 26/27 — corrected API-Football automatic sync

This version replaces the first SPS sync script that tried to call the API once for every player name.

## What changed

- No per-player `/players?search=...` mapping calls.
- Reads season player statistics through `/players?league=...&season=...&page=...`.
- Stops scanning a competition once all SPS names for that competition have been found.
- Keeps Premier League, La Liga, Bundesliga, Serie A, Ligue 1 and Champions League records separate.
- Updates objective stats from API-Football:
  - goals
  - assists
  - successful dribbles
  - key passes / chances created
  - tackles for defenders
  - goalkeeper saves
  - goalkeeper penalty saves
- Updates league-position fields from `/standings` when the player's API team can be identified.
- Preserves manual SPS awards such as Golden Boot, Assist Leader, MVP and Final MVP.
- Preserves `cleanSheets` for now instead of guessing it from aggregate goals-conceded data.
- Uses a persistent API response cache so GitHub Actions does not repeatedly spend API requests on unchanged pages.
- Stores sync state in `data/sync_state.json`.
- Refuses to write an empty roster if the API fails to return usable data.

## Repository files

Keep these files in the root of `SPS26-27`:

- `index.html`
- `players.json`
- `sync.py`
- `config.json`
- `requirements.txt`
- `.github/workflows/sps-sync.yml`
- `data/api_cache.json`
- `data/sync_state.json`

## GitHub secret

The workflow expects this repository Actions secret:

`API_FOOTBALL_KEY`

Never put the API key inside `index.html`, `players.json`, `sync.py`, or `config.json`.

## How the corrected sync works

`API-Football → GitHub Actions → sync.py → players.json → GitHub Pages`

The public SPS website remains view-only. Visitors do not receive the API key.

## Important SPS fields that remain manual

The script does not invent awards. These remain whatever is already stored in `players.json`:

- Golden Boot
- Assist Leader
- MVP
- Final MVP
- Champions League/cup stage when it is not safely derivable from the available season player endpoint
- goalkeeper clean sheets

This prevents the automatic sync from silently changing your SPS award decisions.

## First run

1. Replace the old `sync.py` with the new one.
2. Replace `config.json` with the new one.
3. Replace `.github/workflows/sps-sync.yml` with the included workflow.
4. Upload the included `data` folder.
5. Keep your existing real `players.json` roster. The ZIP contains the current 116-record roster as a safe copy.
6. Commit to `main`.
7. Open GitHub Actions → **SPS automatic sync** → **Run workflow**.

The workflow can take several minutes because it deliberately spaces API requests to stay under the API-Football rate limit.

## If a competition has no API coverage

The sync logs a competition error and continues with the other competitions. The workflow only refuses to write when zero SPS records were updated overall.
