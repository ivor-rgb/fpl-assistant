"""
Thin Supabase client using plain REST calls (PostgREST) rather than the
supabase-py package, one less dependency to install, and the REST API
is stable and simple enough not to need a wrapper.

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY as environment variables
(set as GitHub Actions secrets). Run sql/schema.sql once in the
Supabase SQL editor before first use.
"""
import os
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def _table_url(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"


def is_configured():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def already_processed(team_id, gameweek):
    """Checks the run_log table to avoid sending a duplicate report if
    the workflow happens to run more than once before a deadline."""
    if not is_configured():
        return False
    resp = requests.get(
        _table_url("run_log"),
        headers=HEADERS,
        params={"team_id": f"eq.{team_id}", "gameweek": f"eq.{gameweek}", "select": "id"},
        timeout=15,
    )
    resp.raise_for_status()
    return len(resp.json()) > 0


def log_run(team_id, gameweek):
    if not is_configured():
        return
    requests.post(_table_url("run_log"), headers=HEADERS,
                  json={"team_id": team_id, "gameweek": gameweek}, timeout=15)


def save_recommendation(team_id, gameweek, transfer, starting_xi_summary, chips):
    if not is_configured():
        return
    requests.post(_table_url("recommendations"), headers=HEADERS, json={
        "team_id": team_id,
        "gameweek": gameweek,
        "transfers_out": transfer["transfers_out"],
        "transfers_in": transfer["transfers_in"],
        "hit_taken": transfer["hit_taken"],
        "expected_points_gain": transfer["expected_points_gain"],
        "starting_xi": starting_xi_summary,
        "chip_evaluations": chips,
    }, timeout=15)


def save_player_predictions(gameweek, predictions):
    """
    predictions: list of {"player_id": int, "predicted_points": float}
    Stored so actual points (once the gameweek finishes) can be compared
    against what the model expected, to track model accuracy over time.
    """
    if not is_configured():
        return
    rows = [{"gameweek": gameweek, "player_id": p["player_id"],
             "predicted_points": p["predicted_points"]} for p in predictions]
    # Insert in batches to stay well under any request size limits.
    for i in range(0, len(rows), 500):
        requests.post(_table_url("predictions"), headers=HEADERS,
                      json=rows[i:i + 500], timeout=30)


def backfill_actual_points(bootstrap, gameweek):
    """
    Call this for a gameweek that has since finished, to fill in what
    actually happened next to what was predicted. Best run at the start
    of the *next* week's job, for the week before.
    """
    if not is_configured():
        return
    elements_by_id = {e["id"]: e for e in bootstrap["elements"]}
    for pid, e in elements_by_id.items():
        requests.patch(
            _table_url("predictions"),
            headers=HEADERS,
            params={"gameweek": f"eq.{gameweek}", "player_id": f"eq.{pid}"},
            json={"actual_points": e.get("event_points", 0)},
            timeout=15,
        )


def save_league_snapshot(league_id, league_name, gameweek, standings):
    if not is_configured():
        return
    rows = [{
        "league_id": league_id, "league_name": league_name, "gameweek": gameweek,
        "manager_name": r["entry_name"], "rank": r["rank"], "total_points": r["total"],
    } for r in standings]
    requests.post(_table_url("league_snapshots"), headers=HEADERS, json=rows, timeout=30)
