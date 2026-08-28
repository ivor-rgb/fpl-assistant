"""
Thin client for the (unofficial) Fantasy Premier League API.
No authentication needed for any of these, they're all public GET endpoints.
"""
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (fpl-assistant personal tool)"}


def _get(path):
    resp = requests.get(f"{BASE}/{path}", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_bootstrap():
    """All players, teams, gameweeks (events), and game settings."""
    return _get("bootstrap-static/")


def get_fixtures():
    """Full fixture list for the season, past and future."""
    return _get("fixtures/")


def get_entry(team_id):
    """Basic info about a manager's team."""
    return _get(f"entry/{team_id}/")


def get_picks(team_id, event_id):
    """A manager's squad and captaincy for a specific gameweek."""
    return _get(f"entry/{team_id}/event/{event_id}/picks/")


def get_transfers(team_id):
    """Full transfer history for a manager, used to track price paid."""
    return _get(f"entry/{team_id}/transfers/")


def get_league_standings(league_id):
    """Classic mini-league standings (first page, top entries)."""
    return _get(f"leagues-classic/{league_id}/standings/")


def get_element_summary(player_id):
    """Gameweek-by-gameweek history and upcoming fixtures for one player."""
    return _get(f"element-summary/{player_id}/")


def get_element_summaries_bulk(player_ids, max_workers=15):
    """
    Fetch element-summary for many players concurrently.
    Returns {player_id: summary_dict}. Skips any that fail rather than
    crashing the whole run over one flaky request.
    """
    results = {}

    def fetch_one(pid):
        return pid, get_element_summary(pid)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, pid): pid for pid in player_ids}
        for future in as_completed(futures):
            pid = futures[future]
            try:
                _, summary = future.result()
                results[pid] = summary
            except Exception as exc:
                print(f"  [warn] failed to fetch element-summary for player {pid}: {exc}")

    return results


def get_current_and_next_event(bootstrap):
    """Returns (current_event_id_or_None, next_event_dict_or_None)."""
    current = None
    nxt = None
    for event in bootstrap["events"]:
        if event["is_current"]:
            current = event["id"]
        if event["is_next"]:
            nxt = event
    return current, nxt
