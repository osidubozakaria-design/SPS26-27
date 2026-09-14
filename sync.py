import os, json, time, hashlib
from datetime import datetime, timedelta, timezone
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
CACHE_FILE = os.path.join(ROOT, "data", "api_cache.json")
STATE_FILE = os.path.join(ROOT, "data", "sync_state.json")

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
cache = load_json(CACHE_FILE, {})
state = load_json(STATE_FILE, {"fixture_stats": {}, "player_map": {}, "last_sync": None})

def api_get(endpoint, params):
    """Cached GET. Cache key is endpoint + sorted params."""
    key = endpoint + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    digest = hashlib.sha256(key.encode()).hexdigest()
    if digest in cache:
        return cache[digest]

    url = BASE_URL + endpoint
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    remaining = r.headers.get("x-ratelimit-requests-remaining")
    if remaining is not None:
        print(f"API quota remaining: {remaining}")
    r.raise_for_status()
    payload = r.json()
    if payload.get("errors"):
        raise RuntimeError(f"{endpoint}: {payload['errors']}")
    cache[digest] = payload
    return payload

def norm_name(name):
    return " ".join(str(name).lower().replace("-", " ").split())

def reset_auto_stats(record):
    stats = record.setdefault("stats", {})
    for key in ("goals", "assists", "dribbles", "chancesCreated", "tackles",
                "saves", "penaltySaves", "cleanSheets"):
        stats[key] = 0

def position_group(position):
    p = str(position or "").upper()
    if p in {"ST","RWF","LWF","CAM"}:
        return "attacking"
    if p in {"CM","CDM"}:
        return "midfield"
    if p in {"CB","LB","RB"}:
        return "defender"
    if p == "GK":
        return "goalkeeper"
    return "midfield"

def extract_player_rows(fixture_payload):
    """Return normalized player-performance rows from /fixtures?ids=... response."""
    rows = []
    for item in fixture_payload.get("response", []):
        fixture_id = item.get("fixture", {}).get("id")
        if not fixture_id:
            continue
        for team_block in item.get("players", []) or []:
            team = team_block.get("team", {})
            for pblock in team_block.get("players", []) or []:
                p = pblock.get("player", {}) or {}
                st = pblock.get("statistics") or []
                if not st:
                    continue
                s = st[0] or {}
                goals = s.get("goals") or {}
                passes = s.get("passes") or {}
                dribbles = s.get("dribbles") or {}
                tackles = s.get("tackles") or {}
                duels = s.get("duels") or {}
                shots = s.get("shots") or {}
                games = s.get("games") or {}
                goalkeeping = s.get("goals") or {}
                rows.append({
                    "fixture_id": fixture_id,
                    "player_id": p.get("id"),
                    "name": p.get("name"),
                    "team_id": team.get("id"),
                    "team": team.get("name"),
                    "position": games.get("position"),
                    "minutes": games.get("minutes") or 0,
                    "rating": games.get("rating"),
                    "goals": goals.get("total") or 0,
                    "assists": goals.get("assists") or 0,
                    "dribbles": dribbles.get("success") or 0,
                    "key_passes": passes.get("key") or 0,
                    "tackles": tackles.get("total") or 0,
                    "saves": goalkeeping.get("saves") or 0,
                    "penalty_saves": goalkeeping.get("penalty") or 0,
                    "shots_on": shots.get("on") or 0,
                    "duels_won": duels.get("won") or 0,
                    "clean_sheet": bool((s.get("goals") or {}).get("conceded") == 0)
                        if games.get("minutes") else False
                })
    return rows

def build_name_map():
    """Resolve SPS player names to API player IDs using one request per unique name.
    This is done only for names not already in state."""
    changed = False
    for rec in players:
        name = rec.get("name","").strip()
        key = norm_name(name)
        if not key or key in state["player_map"]:
            continue
        try:
            payload = api_get("/players", {"search": name, "season": CONFIG["season"]})
            matches = payload.get("response", [])
            if matches:
                # Prefer an exact normalized name.
                exact = next((m for m in matches if norm_name(m["player"]["name"]) == key), matches[0])
                state["player_map"][key] = {
                    "id": exact["player"]["id"],
                    "name": exact["player"]["name"]
                }
                print(f"Mapped {name} -> {exact['player']['id']}")
            else:
                state["player_map"][key] = None
            changed = True
        except Exception as e:
            print(f"Player mapping failed for {name}: {e}")
    if changed:
        save_json(STATE_FILE, state)

def sync_competition(label, league_id):
    season = CONFIG["season"]
    lookback = CONFIG["sync"]["lookback_days"]
    today = datetime.now(timezone.utc).date()
    from_date = today - timedelta(days=lookback)

    fixture_list = api_get("/fixtures", {
        "league": league_id,
        "season": season,
        "from": from_date.isoformat(),
        "to": today.isoformat()
    })

    completed_ids = []
    for fx in fixture_list.get("response", []):
        status = (fx.get("fixture", {}).get("status", {}).get("short") or "")
        if status in {"FT", "AET", "PEN"}:
            completed_ids.append(fx["fixture"]["id"])

    print(f"{label}: {len(completed_ids)} completed fixtures in lookback window")

    # Fetch completed match details in batches. API-Football supports multiple IDs.
    batch_size = CONFIG["sync"]["fixture_batch_size"]
    for i in range(0, len(completed_ids), batch_size):
        ids = completed_ids[i:i+batch_size]
        payload = api_get("/fixtures", {"ids": "-".join(map(str, ids))})
        for row in extract_player_rows(payload):
            state["fixture_stats"][str(row["fixture_id"])] = row

    # NOTE: fixture_stats above is a compact player-row cache. For a production
    # version, retain the complete API fixture response if you need every field.

def aggregate_from_cached_rows():
    # Start with automatic stat fields at zero, preserving awards/manual fields.
    for rec in players:
        reset_auto_stats(rec)

    # Build API id -> aggregate row.
    api_id_to_sum = {}
    for row in state["fixture_stats"].values():
        pid = row.get("player_id")
        if not pid:
            continue
        agg = api_id_to_sum.setdefault(pid, {
            "goals":0, "assists":0, "dribbles":0, "chancesCreated":0,
            "tackles":0, "saves":0, "penaltySaves":0, "cleanSheets":0
        })
        agg["goals"] += row.get("goals",0)
        agg["assists"] += row.get("assists",0)
        agg["dribbles"] += row.get("dribbles",0)
        agg["chancesCreated"] += row.get("key_passes",0)
        agg["tackles"] += row.get("tackles",0)
        agg["saves"] += row.get("saves",0)
        agg["penaltySaves"] += row.get("penalty_saves",0)
        if row.get("clean_sheet"):
            agg["cleanSheets"] += 1

    for rec in players:
        mapped = state["player_map"].get(norm_name(rec.get("name","")))
        if not mapped:
            continue
        agg = api_id_to_sum.get(mapped["id"])
        if not agg:
            continue
        group = position_group(rec.get("position"))
        if group == "goalkeeper":
            rec["stats"]["saves"] = agg["saves"]
            rec["stats"]["penaltySaves"] = agg["penaltySaves"]
            rec["stats"]["cleanSheets"] = agg["cleanSheets"]
            rec["stats"]["goals"] = agg["goals"]
            rec["stats"]["assists"] = agg["assists"]
        else:
            rec["stats"]["goals"] = agg["goals"]
            rec["stats"]["assists"] = agg["assists"]
            rec["stats"]["dribbles"] = agg["dribbles"]
            rec["stats"]["chancesCreated"] = agg["chancesCreated"]
            rec["stats"]["tackles"] = agg["tackles"]

def main():
    build_name_map()

    for label, comp in CONFIG["competitions"].items():
        sync_competition(label, comp["league_id"])

    aggregate_from_cached_rows()
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_FILE, state)
    save_json(CACHE_FILE, cache)
    save_json(PLAYERS_FILE, players)

    print(f"Updated {len(players)} SPS records.")
    print(f"Last sync: {state['last_sync']}")

if __name__ == "__main__":
    main()
