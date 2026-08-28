"""
Team-level attack and defence strength, built from actual recent results
rather than FPL's own difficulty rating, which is a blunt 1-5 scale set
before the season and rarely updated.

This uses a simple Poisson-style approach: each team gets an attack
rating (goals scored per match, recent, relative to league average) and
a defence rating (goals conceded per match, recent, relative to league
average). Combining an attacking team's rating with an opponent's
defensive rating gives an expected-goals estimate for that fixture.
"""
import math

LEAGUE_AVG_GOALS_PER_TEAM_PER_MATCH = 1.4  # long-run Premier League average
LAST_N_MATCHES = 6

# Early in a season (or after a promotion/relegation reshuffle) a team
# might have played very few matches, so a couple of freak results can
# swing its rating wildly (e.g. one 0-0 making a "guaranteed" clean
# sheet forever). This shrinks small samples toward the league average
# using PRIOR_MATCHES worth of "average" pseudo-results, fading out
# smoothly as real matches accumulate.
PRIOR_MATCHES = 4


def _finished_fixtures_for_team(fixtures, team_id):
    played = []
    for fx in fixtures:
        if not fx["finished"]:
            continue
        if fx["team_h"] == team_id or fx["team_a"] == team_id:
            played.append(fx)
    played.sort(key=lambda f: f["event"] or 0)
    return played[-LAST_N_MATCHES:]


def compute_team_form(fixtures, teams):
    """
    Returns {team_id: {"attack": float, "defence": float}}
    attack > 1.0 means scoring more than league average recently.
    defence > 1.0 means conceding more than league average recently
    (so a defence rating of 1.0 is average, lower is better).
    """
    form = {}
    for team in teams:
        team_id = team["id"]
        recent = _finished_fixtures_for_team(fixtures, team_id)

        if not recent:
            # No results yet at all - fall back to a fully neutral rating.
            form[team_id] = {"attack": 1.0, "defence": 1.0}
            continue

        goals_for = 0
        goals_against = 0
        for fx in recent:
            if fx["team_h"] == team_id:
                goals_for += fx["team_h_score"] or 0
                goals_against += fx["team_a_score"] or 0
            else:
                goals_for += fx["team_a_score"] or 0
                goals_against += fx["team_h_score"] or 0

        matches = len(recent)
        prior_goals = PRIOR_MATCHES * LEAGUE_AVG_GOALS_PER_TEAM_PER_MATCH

        # Shrunk toward the league average, with the pull fading out as
        # more real matches come in.
        shrunk_for = (goals_for + prior_goals) / (matches + PRIOR_MATCHES)
        shrunk_against = (goals_against + prior_goals) / (matches + PRIOR_MATCHES)

        attack = shrunk_for / LEAGUE_AVG_GOALS_PER_TEAM_PER_MATCH
        defence = shrunk_against / LEAGUE_AVG_GOALS_PER_TEAM_PER_MATCH
        form[team_id] = {"attack": attack, "defence": defence}

    return form


def expected_goals_for_fixture(team_id, opponent_id, team_form):
    """Expected goals for `team_id` against `opponent_id`."""
    attack = team_form[team_id]["attack"]
    opp_defence = team_form[opponent_id]["defence"]
    return attack * opp_defence * LEAGUE_AVG_GOALS_PER_TEAM_PER_MATCH


def clean_sheet_probability(team_id, opponent_id, team_form):
    """Poisson probability of conceding zero goals in this fixture."""
    expected_against = expected_goals_for_fixture(opponent_id, team_id, team_form)
    return math.exp(-expected_against)


def expected_goals_conceded(team_id, opponent_id, team_form):
    return expected_goals_for_fixture(opponent_id, team_id, team_form)
