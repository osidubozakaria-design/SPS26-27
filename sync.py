import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone

import requests

BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY")
if not API_KEY:
    raise SystemExit("Missing API_FOOTBALL_KEY environment variable.")

HEADERS = {"x-apisports-key": API_KEY}
ROOT = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)

PLAYERS_FILE = os.path.join(ROOT, "players.json")
DATA_DIR = os.path.join(ROOT, "data")
CACHE_FILE = os.path.join(DATA_DIR, "api_cache.json")
STATE_FILE = os.path.join(DATA_DIR, "sync_state.json")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


players = load_json(PLAYERS_FILE, [])
if not isinstance(players, list) or not players:
    raise SystemExit("players.json is empty or invalid. Refusing to overwrite it.")

cache = load_json(CACHE_FILE, {})
state = load_json(
    STATE_FILE,
    {
        "last_sync": None,
        "competition_pages": {},
        "unmatched": [],
        "updated_records": 0,
    },
)

# API-Football free plan is commonly limited to 10 requests/minute.
MIN_SECONDS_BETWEEN_REQUESTS = float(CONFIG.get("sync", {}).get("request_delay_seconds", 6.2))
LAST_REQUEST_AT = 0.0


def api_get(endpoint, params):
    """GET with persistent caching and a conservative request limiter."""
    global LAST_REQUEST_AT

    clean_params = {k: v for k, v in params.items() if v is not None}
    key = endpoint + "?" + "&".join(
        f"{k}={clean_params[k]}" for k in sorted(clean_params)
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()

    if digest in cache:
        return cache[digest]

    wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - LAST_REQUEST_AT)
    if wait > 0:
        time.sleep(wait)

    url = BASE_URL + endpoint
    response = requests.get(
        url,
        headers=HEADERS,
        params=clean_params,
        timeout=45,
    )
    LAST_REQUEST_AT = time.monotonic()

    remaining = response.headers.get("x-ratelimit-requests-remaining")
    if remaining is not None:
        print(f"API quota remaining: {remaining}")

    if response.status_code == 401:
        raise RuntimeError("API-Football returned 401: API key is invalid or not authorized.")
    if response.status_code == 403:
        raise RuntimeError("API-Football returned 403: API key/plan is not allowed to use this request.")
    if response.status_code == 429:
        raise RuntimeError("API-Football returned 429: request quota/rate limit was exceeded.")

    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"{endpoint}: {payload['errors']}")

    cache[digest] = payload
    save_json(CACHE_FILE, cache)
    return payload


def strip_accents(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def norm_name(value):
    text = strip_accents(value).lower()
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def name_keys(value):
    normalized = norm_name(value)
    if not normalized:
        return set()
    tokens = tuple(sorted(normalized.split()))
    return {normalized, " ".join(tokens)}


def position_group(position):
    p = str(position or "").upper()
    if p in {"ST", "RWF", "LWF", "CAM"}:
        return "attacking"
    if p in {"CM", "CDM"}:
        return "midfield"
    if p in {"CB", "LB", "RB", "LWB", "RWB"}:
        return "defender"
    if p == "GK":
        return "goalkeeper"
    return "midfield"


def ensure_stats(record):
    stats = record.setdefault("stats", {})
    for key in (
        "goals",
        "assists",
        "dribbles",
        "chancesCreated",
        "tackles",
        "saves",
        "penaltySaves",
        "cleanSheets",
    ):
        stats.setdefault(key, 0)
    return stats


def competition_records(label):
    return [r for r in players if r.get("competition") == label]


def build_roster_name_index(records):
    index = {}
    for record in records:
        for key in name_keys(record.get("name")):
            index.setdefault(key, []).append(record)
    return index


def match_roster_records(api_name, index):
    matches = []
    for key in name_keys(api_name):
        matches.extend(index.get(key, []))
    # Remove duplicates while preserving order.
    seen = set()
    unique = []
    for item in matches:
        rid = item.get("id")
        if rid not in seen:
            seen.add(rid)
            unique.append(item)
    return unique


def extract_player_stat_rows(payload, league_id):
    """Flatten /players?league=&season=&page= results into useful season rows."""
    rows = []
    for item in payload.get("response", []) or []:
        api_player = item.get("player") or {}
        api_player_id = api_player.get("id")
        api_name = api_player.get("name")
        for stat in item.get("statistics", []) or []:
            league = stat.get("league") or {}
            if league.get("id") and int(league.get("id")) != int(league_id):
                continue
            team = stat.get("team") or {}
            games = stat.get("games") or {}
            goals = stat.get("goals") or {}
            passes = stat.get("passes") or {}
            dribbles = stat.get("dribbles") or {}
            tackles = stat.get("tackles") or {}
            rows.append(
                {
                    "player_id": api_player_id,
                    "name": api_name,
                    "team_id": team.get("id"),
                    "team_name": team.get("name"),
                    "position": games.get("position"),
                    "appearances": games.get("appearences") or 0,
                    "minutes": games.get("minutes") or 0,
                    "goals": goals.get("total") or 0,
                    "assists": goals.get("assists") or 0,
                    "dribbles": dribbles.get("success") or 0,
                    "key_passes": passes.get("key") or 0,
                    "tackles": tackles.get("total") or 0,
                    "saves": goals.get("saves") or 0,
                    "penalty_saves": (goals.get("penalty") or {}).get("saved") or 0,
                }
            )
    return rows


def get_all_player_rows(label, league_id, roster_names):
    """Fetch only as many league-season player pages as needed to find the SPS roster."""
    cache_key = f"{label}:{CONFIG['season']}"
    pages = state.setdefault("competition_pages", {})

    first = api_get(
        "/players",
        {"league": league_id, "season": CONFIG["season"], "page": 1},
    )
    total_pages = int((first.get("paging") or {}).get("total") or 1)
    pages[cache_key] = total_pages
    save_json(STATE_FILE, state)

    needed = set()
    for name in roster_names:
        needed.update(name_keys(name))

    all_rows = extract_player_stat_rows(first, league_id)
    matched_keys = set()
    for row in all_rows:
        matched_keys.update(name_keys(row.get("name")))

    print(f"{label}: API-Football reports {total_pages} player pages.")
    print(f"{label}: roster names needed = {len(roster_names)}")

    # The roster is small compared with the full competition player list.
    # Stop as soon as every SPS name has appeared, which saves API requests.
    for page in range(2, total_pages + 1):
        if needed.issubset(matched_keys):
            print(f"{label}: all SPS names found by page {page - 1}; stopping early.")
            break

        payload = api_get(
            "/players",
            {"league": league_id, "season": CONFIG["season"], "page": page},
        )
        page_rows = extract_player_stat_rows(payload, league_id)
        all_rows.extend(page_rows)
        for row in page_rows:
            matched_keys.update(name_keys(row.get("name")))

    if not needed.issubset(matched_keys):
        missing = sorted(needed - matched_keys)
        print(f"{label}: some roster names were not found in the scanned API pages.")
        print(f"{label}: missing normalized name keys: {missing[:20]}")

    return all_rows


def get_standings(league_id):
    """Return team id -> rank for league competitions."""
    payload = api_get("/standings", {"league": league_id, "season": CONFIG["season"]})
    result = payload.get("response") or []
    rank_map = {}
    for block in result:
        for table in block.get("league", {}).get("standings", []) or []:
            for row in table:
                team = row.get("team") or {}
                team_id = team.get("id")
                rank = row.get("rank")
                if team_id is not None and rank is not None:
                    rank_map[int(team_id)] = int(rank)
    return rank_map


def league_points(rank):
    if rank == 1:
        return 5
    if rank == 2:
        return 4
    if rank == 3:
        return 3
    if rank == 4:
        return 2
    if rank == 5:
        return 1
    return 0


def apply_competition(label, comp):
    records = competition_records(label)
    if not records:
        print(f"{label}: no SPS records found; skipping.")
        return 0

    index = build_roster_name_index(records)
    roster_names = [r.get("name", "") for r in records]
    api_rows = get_all_player_rows(label, comp["league_id"], roster_names)

    # Aggregate by SPS record. This keeps Premier League and Champions League
    # records separate even when the same player appears in both.
    aggregates = {}
    team_for_record = {}

    for row in api_rows:
        matched = match_roster_records(row.get("name"), index)
        if not matched:
            continue

        for record in matched:
            rid = record.get("id")
            agg = aggregates.setdefault(
                rid,
                {
                    "goals": 0,
                    "assists": 0,
                    "dribbles": 0,
                    "chancesCreated": 0,
                    "tackles": 0,
                    "saves": 0,
                    "penaltySaves": 0,
                },
            )
            for source, target in (
                ("goals", "goals"),
                ("assists", "assists"),
                ("dribbles", "dribbles"),
                ("key_passes", "chancesCreated"),
                ("tackles", "tackles"),
                ("saves", "saves"),
                ("penalty_saves", "penaltySaves"),
            ):
                agg[target] += int(row.get(source) or 0)

            # Prefer the team row with the most appearances/minutes.
            previous = team_for_record.get(rid)
            current_weight = int(row.get("minutes") or 0) + int(row.get("appearances") or 0) * 100
            if not previous or current_weight > previous["weight"]:
                team_for_record[rid] = {
                    "team_id": row.get("team_id"),
                    "team_name": row.get("team_name"),
                    "weight": current_weight,
                }

    # For league competitions, update the team finishing position when we can
    # identify the player's club. For Champions League we leave the existing
    # stage fields alone because knockout-stage progression is competition-specific.
    rank_map = {}
    if comp.get("kind") == "league":
        rank_map = get_standings(comp["league_id"])

    updated = 0
    unmatched = []
    for record in records:
        rid = record.get("id")
        stats = ensure_stats(record)
        agg = aggregates.get(rid)

        if agg is None:
            unmatched.append(record.get("name"))
            continue

        group = position_group(record.get("position"))

        # These are the objective season stats that API-Football can provide
        # through the league-season /players endpoint.
        stats["goals"] = agg["goals"]
        stats["assists"] = agg["assists"]

        if group in {"attacking", "midfield"}:
            stats["dribbles"] = agg["dribbles"]
            stats["chancesCreated"] = agg["chancesCreated"]

        if group == "defender":
            stats["tackles"] = agg["tackles"]

        if group == "goalkeeper":
            stats["saves"] = agg["saves"]
            stats["penaltySaves"] = agg["penaltySaves"]
            # cleanSheets is deliberately preserved until fixture-level clean
            # sheet calculation is added. We must not guess it from aggregate
            # goals conceded because that would produce incorrect SPS points.

        team = team_for_record.get(rid)
        if team and comp.get("kind") == "league" and team.get("team_id") in rank_map:
            record["leaguePosition"] = rank_map[int(team["team_id"])]

        record["season"] = CONFIG["sps_season_label"]
        updated += 1

    if unmatched:
        unique_unmatched = sorted(set(x for x in unmatched if x))
        print(f"{label}: {len(unique_unmatched)} SPS records did not match API-Football names.")
        for name in unique_unmatched[:20]:
            print(f"  unmatched: {name}")

    print(f"{label}: updated {updated}/{len(records)} SPS records.")
    return updated


def main():
    print(f"Starting SPS automatic sync for {CONFIG['sps_season_label']}...")
    print(f"Roster records: {len(players)}")
    print("Using league-season /players pages; no per-player search mapping is used.")

    total_updated = 0
    failures = []

    for label, comp in CONFIG.get("competitions", {}).items():
        try:
            total_updated += apply_competition(label, comp)
        except Exception as exc:
            failures.append(f"{label}: {exc}")
            print(f"ERROR in {label}: {exc}")

    if total_updated == 0:
        raise SystemExit(
            "No SPS records were updated. Check API_FOOTBALL_KEY, season/competition coverage, "
            "and the workflow log before allowing players.json to be written."
        )

    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    state["updated_records"] = total_updated
    state["unmatched"] = failures
    save_json(STATE_FILE, state)
    save_json(CACHE_FILE, cache)
    save_json(PLAYERS_FILE, players)

    if failures:
        print("Completed with warnings:")
        for failure in failures:
            print(f"  {failure}")
    else:
        print("Completed successfully with no competition-level errors.")

    print(f"Updated {total_updated} SPS records.")
    print(f"Last sync: {state['last_sync']}")


if __name__ == "__main__":
    main()
