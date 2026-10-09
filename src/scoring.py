"""
Expected points (xP) model.

For each player, for each upcoming fixture in the horizon, this estimates
FPL points from:
  - attacking output: recent per-90 xG and xA, scaled by expected minutes
    and by how leaky the opponent is (and home or away)
  - clean sheet / goals conceded: from team_strength, adjusted for venue
    (GK and DEF 4 points, MID 1 point)
  - defensive contribution: how often he hits the threshold, smoothed
    toward the league rate for his position
  - bonus: his season bonus rate, smoothed toward the league rate for his
    position (bonus is very noisy, so the smoothing is strong)
  - availability: injury/suspension flags dampen expected minutes
  - double gameweeks: each fixture is scored and the points are added up

This is a weighted heuristic built from public underlying stats, not a
trained model. It is deliberately transparent so each number can be
sanity-checked against what actually happened.
"""
import math

from team_strength import expected_goals_conceded

FORM_WINDOW = 6          # gameweeks of history to look back over
RECENT_WEIGHTS = [1, 1, 2, 2, 3, 4]  # most recent match weighted highest

# Same small-sample problem as team ratings, but at player level: one
# fluke goal in a cameo appearance can extrapolate into an absurd
# per-90 rate. This shrinks each player's xG/xA per-90 toward a
# position-average prior, weighted by minutes played so far, fading
# out as more real minutes accumulate. PRIOR_MINUTES = 270 is roughly
# three full matches worth of "average player at this position".
PRIOR_MINUTES = 270
PRIOR_XG_PER90 = {1: 0.00, 2: 0.03, 3: 0.12, 4: 0.35}
PRIOR_XA_PER90 = {1: 0.00, 2: 0.05, 3: 0.15, 4: 0.10}

# League-wide baselines measured from the 2026/27 season so far, per
# appearance of 60+ minutes. Bonus and defensive contribution are both
# streaky, so a player's own numbers are blended with these. The pull is
# worth BONUS_PRIOR_GAMES / DC_PRIOR_GAMES "average" appearances and fades
# as he plays more.
PRIOR_BONUS_PER_GAME = {1: 0.27, 2: 0.24, 3: 0.31, 4: 0.52}
PRIOR_DC_HIT_RATE = {1: 0.0, 2: 0.26, 3: 0.13, 4: 0.01}
BONUS_PRIOR_GAMES = 8
DC_PRIOR_GAMES = 6

# Home teams score about 8% more than the league average, away teams about
# 8% less. Used both for goals and assists and for clean sheet chances.
HOME_GOALS_FACTOR = 1.08
AWAY_GOALS_FACTOR = 0.92
# Keeps one extreme opponent rating from swinging a player too far.
FIXTURE_FACTOR_MIN = 0.6
FIXTURE_FACTOR_MAX = 1.5

GOAL_POINTS = {1: 6, 2: 6, 3: 5, 4: 4}          # element_type -> points per goal
CLEAN_SHEET_POINTS = {1: 4, 2: 4, 3: 1, 4: 0}   # element_type -> points per clean sheet
ASSIST_POINTS = 3
GOALS_CONCEDED_PENALTY_PER_2 = {1: 1, 2: 1, 3: 0, 4: 0}  # -1 per 2 conceded, GK/DEF only
SAVE_POINTS_PER_3 = 1  # GK only

# Defensive contribution points: 2 points if a player's combined
# tackles + clearances/blocks/interceptions + recoveries hits the
# threshold for their position. Goalkeepers don't earn these.
DC_THRESHOLD = {1: None, 2: 10, 3: 12, 4: 12}
DC_POINTS_AWARDED = 2


def _weighted_avg(values, weights):
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None]
    if not pairs:
        return 0.0
    total_w = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total_w


def _dc_points_for_row(row, pos):
    threshold = DC_THRESHOLD.get(pos)
    if threshold is None:
        return 0
    combined = row.get("defensive_contribution", 0) or 0
    return DC_POINTS_AWARDED if combined >= threshold else 0


def _player_recent_form(history, pos):
    """
    Takes a player's element-summary 'history' list (gameweek dicts,
    oldest first) and returns per-90 rates plus playing-time signals
    from the most recent FORM_WINDOW appearances.
    """
    recent = history[-FORM_WINDOW:]
    weights = RECENT_WEIGHTS[-len(recent):]

    minutes_list = [h["minutes"] for h in recent]
    starts_list = [h["starts"] for h in recent]

    # Shrunk per-90 rate: accumulate total stat and total minutes across
    # all recent appearances (weighting each match's contribution by its
    # own minutes, so a 90-minute match naturally counts more than a
    # 10-minute cameo), then blend with a position-average prior worth
    # PRIOR_MINUTES of playing time. This fades out as real minutes
    # accumulate, so it barely matters by mid-season but stops single
    # fluke matches early on from producing an absurd extrapolated rate.
    total_minutes_played = sum(h["minutes"] for h in recent)

    def shrunk_per90(field, prior_rate):
        total_stat = sum(float(h[field]) for h in recent)
        numerator = total_stat * 90.0 + prior_rate * PRIOR_MINUTES
        denominator = total_minutes_played + PRIOR_MINUTES
        return numerator / denominator

    avg_minutes = _weighted_avg(minutes_list, weights)
    avg_starts = _weighted_avg(starts_list, weights)
    avg_saves = _weighted_avg([h["saves"] for h in recent], weights)

    # Bonus and defensive contribution use the whole season (not just the
    # last six games) and only full appearances, then are blended with the
    # league rate for the position so a couple of big games can't dominate.
    full = [h for h in history if h["minutes"] >= 60]
    bonus_rate = (
        sum(h["bonus"] for h in full) + PRIOR_BONUS_PER_GAME[pos] * BONUS_PRIOR_GAMES
    ) / (len(full) + BONUS_PRIOR_GAMES)
    dc_hits = sum(1 for h in full if _dc_points_for_row(h, pos) > 0)
    dc_rate = (
        dc_hits + PRIOR_DC_HIT_RATE[pos] * DC_PRIOR_GAMES
    ) / (len(full) + DC_PRIOR_GAMES)
    avg_bonus = bonus_rate
    avg_dc_points = DC_POINTS_AWARDED * dc_rate

    return {
        "xg_per90": shrunk_per90("expected_goals", PRIOR_XG_PER90[pos]),
        "xa_per90": shrunk_per90("expected_assists", PRIOR_XA_PER90[pos]),
        "avg_minutes": avg_minutes,
        "avg_starts": avg_starts,
        "avg_dc_points": avg_dc_points,
        "avg_bonus": avg_bonus,
        "avg_saves": avg_saves,
    }


def _drop_recent_absences(history, element):
    """
    A player who has just returned from a short injury or suspension would
    otherwise be marked down twice: the live FPL flag says he's fit, but the
    0-minute games he missed still drag his average minutes down (a regular
    starter missing one game would be treated as a 60-minute player).

    If FPL now lists him as fully available, and his 1 to 3 most recent
    gameweeks are 0-minute games that followed a run of regular starts
    (averaging 60+ minutes over the 3 games before), those missed games are
    treated as an absence and left out of the form calculation.
    """
    if element.get("status") != "a" or element.get("chance_of_playing_next_round") not in (None, 100):
        return history
    trailing = 0
    while trailing < len(history) and history[-1 - trailing]["minutes"] == 0:
        trailing += 1
    if trailing == 0 or trailing > 3:
        return history
    before = history[: len(history) - trailing]
    if len(before) < 3 or sum(h["minutes"] for h in before[-3:]) / 3 < 60:
        return history
    return before


def _availability_multiplier(element):
    """Dampens expected minutes for injury/suspension doubt."""
    status = element.get("status", "a")
    if status in ("i", "s", "u", "n"):  # injured, suspended, unavailable, not in squad
        return 0.0
    chance = element.get("chance_of_playing_next_round")
    if chance is None:
        return 1.0
    return chance / 100.0


def _fixture_attack_factor(opponent_id, is_home, team_form):
    """
    How much easier or harder than normal this fixture is for scoring:
    the opponent's recent goals conceded relative to the league average
    (above 1 means a leaky defence), times a home or away adjustment.
    """
    opp_defence = team_form[opponent_id]["defence"]
    venue = HOME_GOALS_FACTOR if is_home else AWAY_GOALS_FACTOR
    return max(FIXTURE_FACTOR_MIN, min(FIXTURE_FACTOR_MAX, opp_defence * venue))


def _future_fixtures_for_team(fixtures, team_id, from_event, horizon_gws):
    upcoming = []
    for fx in fixtures:
        if fx["finished"]:
            continue
        if fx["event"] is None or fx["event"] < from_event:
            continue
        if fx["event"] >= from_event + horizon_gws:
            continue
        if fx["team_h"] == team_id or fx["team_a"] == team_id:
            upcoming.append(fx)
    return upcoming


def build_expected_points(bootstrap, fixtures, element_summaries, team_form,
                           from_event, horizon_gws, decay=0.85):
    """
    Returns {player_id: {"per_gw": {event_id: xpts}, "total": float,
                          "form": {...}}}
    `total` is the decay-weighted sum across the horizon, used for
    transfer and chip decisions. `per_gw` is used for picking this
    week's starting XI and captain.
    """
    elements_by_id = {e["id"]: e for e in bootstrap["elements"]}
    scores = {}

    for pid, element in elements_by_id.items():
        summary = element_summaries.get(pid)
        history = summary["history"] if summary else []
        history = _drop_recent_absences(history, element)
        pos = element["element_type"]
        form = _player_recent_form(history, pos) if history else {
            "xg_per90": 0.0, "xa_per90": 0.0, "avg_minutes": 0.0,
            "avg_starts": 0.0, "avg_dc_points": 0.0, "avg_bonus": 0.0,
            "avg_saves": 0.0,
        }

        availability = _availability_multiplier(element)
        # Blend recent minutes trend with the live availability flag,
        # capped at 90.
        expected_minutes = min(form["avg_minutes"], 90) * availability
        minutes_factor = expected_minutes / 90.0

        team_id = element["team"]

        team_fixtures = _future_fixtures_for_team(fixtures, team_id, from_event, horizon_gws)

        per_gw = {}
        for fx in team_fixtures:
            is_home = fx["team_h"] == team_id
            opponent_id = fx["team_a"] if is_home else fx["team_h"]

            fixture_factor = _fixture_attack_factor(opponent_id, is_home, team_form)
            attacking_pts = minutes_factor * fixture_factor * (
                GOAL_POINTS[pos] * form["xg_per90"] + ASSIST_POINTS * form["xa_per90"]
            )

            # Clean sheets: GK and DEF get 4 points, MID gets 1. Only GK and
            # DEF lose points for goals conceded. The opponent scores a bit
            # more at home and a bit less away.
            cs_prob = 0.0
            gc_penalty = 0.0
            if pos in (1, 2, 3):
                opp_venue = AWAY_GOALS_FACTOR if is_home else HOME_GOALS_FACTOR
                xgc = expected_goals_conceded(team_id, opponent_id, team_form) * opp_venue
                cs_prob = math.exp(-xgc)
                gc_penalty = (xgc / 2.0) * GOALS_CONCEDED_PENALTY_PER_2[pos] * minutes_factor

            cs_pts = minutes_factor * CLEAN_SHEET_POINTS[pos] * cs_prob

            save_pts = 0.0
            if pos == 1:
                save_pts = minutes_factor * (form["avg_saves"] / 3.0) * SAVE_POINTS_PER_3

            dc_pts = minutes_factor * form["avg_dc_points"]
            bonus_pts = minutes_factor * form["avg_bonus"]

            # Appearance points: 1 for <60 mins played, 2 for 60+.
            appearance_pts = 2.0 * minutes_factor if minutes_factor > 0 else 0.0

            xpts = (attacking_pts + cs_pts - gc_penalty + save_pts
                    + dc_pts + bonus_pts + appearance_pts)
            # In a double gameweek a team has two fixtures in one event, so
            # the points from each game are added together.
            per_gw[fx["event"]] = round(per_gw.get(fx["event"], 0.0) + xpts, 2)

        # Decay-weighted total across the horizon for transfer/chip decisions.
        total = 0.0
        for i, gw in enumerate(sorted(per_gw.keys())):
            total += per_gw[gw] * (decay ** i)

        scores[pid] = {"per_gw": per_gw, "total": round(total, 2), "form": form}

    return scores
