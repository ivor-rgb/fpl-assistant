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
import unicodedata
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


def _norm(text):
    """Lower-case, strip accents, so 'Joao Pedro' matches 'João Pedro'."""
    text = text.replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode().lower().strip()


def _find_players(query, bootstrap):
    """Match 'Tarkowski', 'Joao Pedro' or 'Gabriel (ARS)' to FPL players."""
    team_filter = None
    query = query.strip()
    if query.endswith(")") and "(" in query:
        query, team_filter = query.rsplit("(", 1)
        team_filter = team_filter.rstrip(") ").strip().upper()
    q = _norm(query)
    short = {t["id"]: t["short_name"].upper() for t in bootstrap["teams"]}
    matches = [
        e for e in bootstrap["elements"]
        if _norm(e["web_name"]) == q
        or _norm(e["second_name"]) == q
        or _norm(f"{e['first_name']} {e['second_name']}") == q
    ]
    if team_filter:
        matches = [e for e in matches if short[e["team"]] == team_filter]
    return matches


def build_current_squad(picks_data, api_transfers, next_event_id, manual_transfers,
                        bootstrap, elements_by_id):
    """
    The FPL picks endpoint only shows the squad as it was at the last
    deadline, so transfers you've made since then are missing. This starts
    from that squad and applies, in order:
      1. any transfers the FPL API already lists for the upcoming gameweek
      2. any you've listed in settings.json under "transfers_made",
         written as "Out > In" (add the club if a surname is shared,
         e.g. "Gabriel (ARS)")
    Returns (squad_ids, bank, applied, warnings). `applied` is a list of
    (out_id, in_id). If the result isn't a valid squad, everything is
    ignored and the last-deadline squad is returned with a warning.
    """
    squad = [p["element"] for p in picks_data["picks"]]
    bank = picks_data["entry_history"]["bank"]
    original_squad, original_bank = list(squad), bank
    applied, warnings = [], []

    pending = sorted(
        (t for t in api_transfers if t["event"] == next_event_id),
        key=lambda t: t["time"],
    )
    for t in pending:
        if t["element_out"] in squad and t["element_in"] not in squad:
            squad.remove(t["element_out"])
            squad.append(t["element_in"])
            bank += t["element_out_cost"] - t["element_in_cost"]
            applied.append((t["element_out"], t["element_in"]))

    for entry in manual_transfers:
        if ">" not in entry:
            warnings.append(f"Couldn't read '{entry}'. Write transfers as 'Out > In'.")
            continue
        out_name, in_name = [part.strip() for part in entry.split(">", 1)]
        out_matches = _find_players(out_name, bootstrap)
        in_matches = _find_players(in_name, bootstrap)
        out_in_squad = [e for e in out_matches if e["id"] in squad]
        in_in_squad = [e for e in in_matches if e["id"] in squad]

        if not out_in_squad and in_in_squad:
            continue  # already applied (the API now shows it too)
        if not out_matches:
            warnings.append(f"Couldn't find a player called '{out_name}' (from '{entry}'). Check the spelling.")
            continue
        if not out_in_squad:
            warnings.append(f"'{out_name}' (from '{entry}') isn't in your squad.")
            continue
        if len(out_in_squad) > 1:
            warnings.append(f"'{out_name}' (from '{entry}') matches more than one player in your squad. Add the club, e.g. 'Gabriel (ARS)'.")
            continue
        if not in_matches:
            warnings.append(f"Couldn't find a player called '{in_name}' (from '{entry}'). Check the spelling.")
            continue
        if len(in_matches) > 1:
            warnings.append(f"'{in_name}' (from '{entry}') matches more than one player. Add the club, e.g. 'Gabriel (ARS)'.")
            continue
        out_e, in_e = out_in_squad[0], in_matches[0]
        if in_e["id"] in squad:
            warnings.append(f"{in_e['web_name']} (from '{entry}') is already in your squad.")
            continue
        squad.remove(out_e["id"])
        squad.append(in_e["id"])
        # Selling prices aren't public, so current prices are used here.
        bank += out_e["now_cost"] - in_e["now_cost"]
        applied.append((out_e["id"], in_e["id"]))

    if len(squad) != 15 or not optimizer._squad_valid(squad, elements_by_id):
        warnings.append(
            "Those transfers don't leave a valid squad (check positions and the "
            "3 per club limit), so they've been ignored and your last gameweek "
            "squad is used."
        )
        return original_squad, original_bank, [], warnings
    if bank < 0:
        warnings.append(
            "Your bank comes out negative, so one of these transfers may not be "
            "affordable, or selling prices differ from current prices."
        )
    return squad, bank, applied, warnings


def squad_notes_markdown(gameweek, last_event, applied, warnings, bank,
                         free_start, free_left, elements_by_id):
    name = lambda pid: elements_by_id[pid]["web_name"]
    lines = ["## Squad used for this report"]
    if applied:
        lines.append(f"Your GW{last_event} squad plus {len(applied)} transfer(s) made for GW{gameweek}:")
        for out_id, in_id in applied:
            lines.append(f"- {name(out_id)} > {name(in_id)}")
    else:
        lines.append(
            f"No transfers found for GW{gameweek}, so this uses your GW{last_event} "
            "squad as it stands. If you've made transfers, add them to "
            "`transfers_made` in settings.json (for example `\"Collins > Tarkowski\"`) "
            "and run again."
        )
    lines.append("")
    lines.append(
        f"Bank: £{bank / 10:.1f}m. Free transfers: {free_start} at the start of "
        f"the week, {len(applied)} used, {free_left} left."
    )
    for warning in warnings:
        lines.append("")
        lines.append(f"**Heads up:** {warning}")
    return "\n".join(lines)


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
    try:
        api_transfers = fpl_api.get_transfers(team_id)
    except Exception as exc:
        print(f"  [warn] couldn't fetch transfer history: {exc}")
        api_transfers = []
    squad_ids, bank, applied_transfers, squad_warnings = build_current_squad(
        picks_data, api_transfers, next_event["id"],
        settings.get("transfers_made", []), bootstrap, elements_by_id,
    )

    # The public API doesn't expose "free transfers currently available"
    # directly, only a season-long transfer count, and working it out
    # properly means replicating FPL's rollover/wildcard rules from your
    # full transfer history. Rather than guess, this is read from
    # settings.json, update it yourself each week (it's shown on the
    # FPL site's transfers page) until a future version calculates it.
    # "free_transfers" in settings.json is how many you had at the START of
    # the gameweek. Transfers you've already made use some of them up.
    free_start = settings.get("free_transfers", 1)
    free_transfers = max(0, free_start - len(applied_transfers))

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
        max_transfers_considered=3, hit_cost=FREE_TRANSFER_HIT_COST,
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
    notes = squad_notes_markdown(
        next_event["id"], current_event, applied_transfers, squad_warnings,
        bank, free_start, free_transfers, elements_by_id,
    )
    report_lines = html.split("\n")
    insert_at = next((i + 1 for i, line in enumerate(report_lines)
                      if line.startswith("**Deadline:**")), 0)
    report_lines[insert_at:insert_at] = ["", notes]
    html = "\n".join(report_lines)

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
