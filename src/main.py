"""
Weekly entry point. Fetches live data, scores every player, works out
the best starting XI and transfer for the upcoming gameweek, evaluates
chips, saves history to Supabase, and emails the report.

Run manually with: python src/main.py
Run automatically via .github/workflows/weekly.yml
"""
import json
import os
import sys
from datetime import datetime, timezone

import fpl_api
import team_strength
import scoring
import optimizer
import report
import supabase_store

HORIZON_GWS = 5
DECAY = 0.85
FREE_TRANSFER_HIT_COST = 4
DEADLINE_WARNING_HOURS = 60  # only send if the deadline is within this many hours


def load_settings():
    settings_path = os.path.join(os.path.dirname(__file__), "..", "settings.json")
    with open(settings_path) as f:
        return json.load(f)


def hours_until(deadline_iso):
    deadline = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return (deadline - now).total_seconds() / 3600.0


def main():
    settings = load_settings()
    team_id = settings["team_id"]
    league_ids = settings.get("league_ids", [])
    force_run = "--force" in sys.argv

    print("Fetching bootstrap data...")
    bootstrap = fpl_api.get_bootstrap()
    fixtures = fpl_api.get_fixtures()
    current_event, next_event = fpl_api.get_current_and_next_event(bootstrap)

    if next_event is None:
        print("No upcoming gameweek found (season may be over). Exiting.")
        return

    hrs = hours_until(next_event["deadline_time"])
    print(f"Next deadline: GW{next_event['id']} in {hrs:.1f} hours")
    if not force_run and hrs > DEADLINE_WARNING_HOURS:
        print("Not within the reporting window yet, exiting without sending anything.")
        return

    if not force_run and supabase_store.already_processed(team_id, next_event["id"]):
        print("Already generated a report for this gameweek, exiting to avoid a duplicate.")
        return

    elements_by_id = {e["id"]: e for e in bootstrap["elements"]}

    print("Fetching your current squad...")
    picks_data = fpl_api.get_picks(team_id, current_event)
    squad_ids = [p["element"] for p in picks_data["picks"]]
    bank = picks_data["entry_history"]["bank"]

    # The public API doesn't expose "free transfers currently available"
    # directly, only a season-long transfer count, and working it out
    # properly means replicating FPL's rollover/wildcard rules from your
    # full transfer history. Rather than guess, this is read from
    # settings.json, update it yourself each week (it's shown on the
    # FPL site's transfers page) until a future version calculates it.
    free_transfers = settings.get("free_transfers", 1)

    print("Computing team strength ratings...")
    team_form = team_strength.compute_team_form(fixtures, bootstrap["teams"])

    print("Fetching player histories (this is the slow part, ~1-2 minutes)...")
    all_player_ids = list(elements_by_id.keys())
    summaries = fpl_api.get_element_summaries_bulk(all_player_ids, max_workers=20)

    print("Scoring every player...")
    scores = scoring.build_expected_points(
        bootstrap, fixtures, summaries, team_form,
        from_event=next_event["id"], horizon_gws=HORIZON_GWS, decay=DECAY,
    )
    xp_totals = {pid: s["total"] for pid, s in scores.items()}
    gw_scores = {pid: s["per_gw"].get(next_event["id"], 0) for pid, s in scores.items()}

    print("Working out the best starting XI...")
    xi = optimizer.best_starting_xi(squad_ids, elements_by_id, gw_scores)

    print("Searching for the best transfer scenarios...")
    transfer_scenarios = optimizer.suggest_transfers(
        squad_ids, bank, free_transfers, elements_by_id, xp_totals,
        max_transfers_considered=2, hit_cost=FREE_TRANSFER_HIT_COST,
    )

    print("Evaluating chips...")
    blank_gw_ids = set()  # left empty for now; a future improvement is
    # detecting fixture-less teams for the upcoming gameweek specifically.
    chips = optimizer.evaluate_chips(
        squad_ids, elements_by_id, gw_scores, xp_totals, bank, free_transfers,
        blank_gw_player_ids=blank_gw_ids,
    )

    print("Fetching mini-league standings...")
    league_snapshots = []
    for league_id in league_ids:
        try:
            standings_data = fpl_api.get_league_standings(league_id)
            league_snapshots.append({
                "name": standings_data["league"]["name"],
                "standings": standings_data["standings"]["results"],
            })
            supabase_store.save_league_snapshot(
                league_id, standings_data["league"]["name"],
                next_event["id"], standings_data["standings"]["results"],
            )
        except Exception as exc:
            print(f"  [warn] couldn't fetch league {league_id}: {exc}")

    print("Building the report...")
    deadline_str = next_event["deadline_time"]
    context = {
        "gameweek": next_event["id"],
        "deadline": deadline_str,
        "elements_by_id": elements_by_id,
        "transfer_scenarios": transfer_scenarios,
        "starting_xi": xi,
        "gw_scores": gw_scores,
        "chips": chips,
        "league_snapshots": league_snapshots,
    }
    html = report.build_markdown_report(context)

    print("Writing report to the repo and the Actions summary...")
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    report_path = report.write_report_files(html, next_event["id"], repo_root)
    print(f"  wrote {report_path}")

    print("Saving history to Supabase...")
    starting_xi_summary = {
        "formation": xi["formation"],
        "starting_xi": [elements_by_id[pid]["web_name"] for pid in xi["starting_xi"]],
        "captain": elements_by_id[xi["captain"]]["web_name"],
    }
    supabase_store.save_recommendation(team_id, next_event["id"], transfer_scenarios, starting_xi_summary, chips)
    predictions = [{"player_id": pid, "predicted_points": pts} for pid, pts in gw_scores.items()]
    supabase_store.save_player_predictions(next_event["id"], predictions)

    # Backfill actual results for the gameweek that just finished, now
    # that its data has settled.
    if current_event:
        supabase_store.backfill_actual_points(bootstrap, current_event)

    supabase_store.log_run(team_id, next_event["id"])
    print("Done.")


if __name__ == "__main__":
    main()
