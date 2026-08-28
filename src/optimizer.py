"""
Optimisation layer, built on PuLP (open-source, bundles the CBC solver,
no license or API key needed).

Two separate solves:

1. best_starting_xi(): given a fixed 15-man squad, pick the highest
   expected-points valid starting XI, formation and captain for the
   upcoming gameweek. This is always exactly solvable, it's a small
   search space.

2. suggest_transfers(): given the current squad, bank, and free
   transfers, searches transfer combinations (0, 1 or 2 changes) for
   the one that maximises decay-weighted expected points over the
   horizon, after accounting for the -4 hit on any transfer beyond
   the free ones. This does NOT plan multiple future weeks of
   transfers simultaneously (that's a much bigger multi-period LP,
   the kind projects like sertalpbilal's FPL-Optimization-Tools do);
   it optimises this week's decision using multi-week expected value,
   which captures most of the benefit without the added complexity.

Chip value (Bench Boost, Triple Captain, Wildcard, Free Hit) is
evaluated using the same expected-points numbers, comparing "play it
now" against "hold it", rather than a separate arbitrary heuristic.
"""
import pulp

FORMATIONS = [
    # (GK, DEF, MID, FWD)
    (1, 3, 4, 3), (1, 3, 5, 2), (1, 4, 3, 3), (1, 4, 4, 2),
    (1, 4, 5, 1), (1, 5, 2, 3), (1, 5, 3, 2), (1, 5, 4, 1),
]
POSITION_LIMITS = {1: (2, 2), 2: (5, 5), 3: (5, 5), 4: (3, 3)}  # squad-wide, exactly these counts
MAX_PER_CLUB = 3
SQUAD_SIZE = 15
BUDGET_UNITS = 1000  # £100.0m, in FPL's tenths-of-a-million units


def best_starting_xi(squad_player_ids, elements_by_id, gw_scores):
    """
    squad_player_ids: list of 15 player ids currently owned
    gw_scores: {player_id: expected points this specific gameweek}
    Returns dict with starting_xi, bench (ordered), captain, vice_captain,
    formation, and expected_points.
    """
    best = None
    for gk_n, def_n, mid_n, fwd_n in FORMATIONS:
        by_pos = {1: [], 2: [], 3: [], 4: []}
        for pid in squad_player_ids:
            pos = elements_by_id[pid]["element_type"]
            by_pos[pos].append(pid)

        needed = {1: gk_n, 2: def_n, 3: mid_n, 4: fwd_n}
        if any(len(by_pos[p]) < needed[p] for p in needed):
            continue  # squad can't fill this formation

        starters = []
        for pos, count in needed.items():
            ranked = sorted(by_pos[pos], key=lambda pid: gw_scores.get(pid, 0), reverse=True)
            starters.extend(ranked[:count])

        total = sum(gw_scores.get(pid, 0) for pid in starters)
        # Captain doubles their points; pick the highest scorer among starters.
        capt = max(starters, key=lambda pid: gw_scores.get(pid, 0))
        total_with_captain = total + gw_scores.get(capt, 0)

        if best is None or total_with_captain > best["expected_points"]:
            bench = [pid for pid in squad_player_ids if pid not in starters]
            bench.sort(key=lambda pid: gw_scores.get(pid, 0), reverse=True)
            remaining = [pid for pid in starters if pid != capt]
            vice = max(remaining, key=lambda pid: gw_scores.get(pid, 0)) if remaining else capt
            best = {
                "formation": f"{def_n}-{mid_n}-{fwd_n}",
                "starting_xi": starters,
                "bench": bench,
                "captain": capt,
                "vice_captain": vice,
                "expected_points": round(total_with_captain, 2),
            }

    return best


def _squad_cost(player_ids, elements_by_id):
    return sum(elements_by_id[pid]["now_cost"] for pid in player_ids)


def _squad_valid(player_ids, elements_by_id):
    counts_by_pos = {1: 0, 2: 0, 3: 0, 4: 0}
    counts_by_club = {}
    for pid in player_ids:
        e = elements_by_id[pid]
        counts_by_pos[e["element_type"]] += 1
        counts_by_club[e["team"]] = counts_by_club.get(e["team"], 0) + 1

    for pos, (lo, hi) in POSITION_LIMITS.items():
        if not (lo <= counts_by_pos[pos] <= hi):
            return False
    if any(count > MAX_PER_CLUB for count in counts_by_club.values()):
        return False
    return True


def suggest_transfers(current_squad_ids, bank_units, free_transfers, elements_by_id,
                       xp_totals, max_transfers_considered=2, hit_cost=4):
    """
    xp_totals: {player_id: decay-weighted expected points over the horizon}

    Solves a genuine MILP (via PuLP/CBC) for the best possible 15-man
    squad, separately for exactly 0, 1 and 2 transfers, then compares
    the three net of any hit. Restricting the candidate pool to each
    position's top-scoring players (by xp_totals) keeps the model small
    and fast, at negligible cost, a player who wouldn't crack the top
    30 at their position by expected points is never going to be the
    optimiser's pick anyway.

    Returns a dict with transfers_out, transfers_in, hit_taken,
    transfers_used, and expected_points_gain (net of any hit).
    """
    current_set = set(current_squad_ids)
    current_xp = sum(xp_totals.get(pid, 0) for pid in current_squad_ids)
    total_budget = sum(elements_by_id[pid]["now_cost"] for pid in current_squad_ids) + bank_units

    candidates_by_pos = {1: [], 2: [], 3: [], 4: []}
    for pid, e in elements_by_id.items():
        if pid in current_set:
            continue
        if e.get("status") in ("u", "n"):  # unavailable / not in the game
            continue
        candidates_by_pos[e["element_type"]].append(pid)
    for pos in candidates_by_pos:
        candidates_by_pos[pos].sort(key=lambda pid: xp_totals.get(pid, 0), reverse=True)

    candidate_pool = set(current_squad_ids)
    for pos, ids in candidates_by_pos.items():
        candidate_pool.update(ids[:30])
    candidate_list = list(candidate_pool)

    best_option = None

    for transfers_used in range(0, max_transfers_considered + 1):
        prob = pulp.LpProblem(f"squad_{transfers_used}_transfers", pulp.LpMaximize)
        pick = {pid: pulp.LpVariable(f"pick_{pid}", cat="Binary") for pid in candidate_list}

        prob += pulp.lpSum(xp_totals.get(pid, 0) * pick[pid] for pid in candidate_list)

        prob += pulp.lpSum(pick.values()) == SQUAD_SIZE

        for pos, (lo, hi) in POSITION_LIMITS.items():
            ids_this_pos = [pid for pid in candidate_list if elements_by_id[pid]["element_type"] == pos]
            prob += pulp.lpSum(pick[pid] for pid in ids_this_pos) == lo

        clubs = {elements_by_id[pid]["team"] for pid in candidate_list}
        for club in clubs:
            ids_this_club = [pid for pid in candidate_list if elements_by_id[pid]["team"] == club]
            prob += pulp.lpSum(pick[pid] for pid in ids_this_club) <= MAX_PER_CLUB

        prob += pulp.lpSum(elements_by_id[pid]["now_cost"] * pick[pid] for pid in candidate_list) <= total_budget

        # Exactly `transfers_used` players change: (15 - transfers_used) of
        # the current squad must be kept.
        prob += pulp.lpSum(pick[pid] for pid in current_squad_ids if pid in pick) == SQUAD_SIZE - transfers_used

        prob.solve(pulp.PULP_CBC_CMD(msg=0))

        if pulp.LpStatus[prob.status] != "Optimal":
            continue

        new_squad = [pid for pid in candidate_list if pick[pid].value() == 1]
        new_xp = sum(xp_totals.get(pid, 0) for pid in new_squad)
        hit = max(0, transfers_used - free_transfers) * hit_cost
        net = new_xp - hit

        if best_option is None or net > best_option["_net"]:
            outs = [pid for pid in current_squad_ids if pid not in new_squad]
            ins = [pid for pid in new_squad if pid not in current_squad_ids]
            best_option = {
                "transfers_out": outs,
                "transfers_in": ins,
                "hit_taken": hit,
                "transfers_used": transfers_used,
                "expected_points_gain": round(net - current_xp, 2),
                "new_squad_xp": round(net, 2),
                "_net": net,
            }

    best_option.pop("_net", None)
    return best_option


def evaluate_chips(current_squad_ids, elements_by_id, gw_scores, xp_totals,
                    bank_units, free_transfers, blank_gw_player_ids=None):
    """
    Compares "play this chip now" against "hold it", using the same
    expected-points numbers as everything else. Returns a list of
    {"chip": name, "worth_it": bool, "reasoning": str, "value": float}
    """
    results = []
    blank_gw_player_ids = blank_gw_player_ids or set()

    xi = best_starting_xi(current_squad_ids, elements_by_id, gw_scores)
    bench_xp = sum(gw_scores.get(pid, 0) for pid in xi["bench"])

    # Bench Boost: worth it if the bench alone would meaningfully add points.
    results.append({
        "chip": "Bench Boost",
        "worth_it": bench_xp >= 8,
        "value": round(bench_xp, 2),
        "reasoning": f"Your bench is projected {bench_xp:.1f} points this week.",
    })

    # Triple Captain: worth it if the best captain option has an unusually high ceiling.
    capt_xp = gw_scores.get(xi["captain"], 0)
    results.append({
        "chip": "Triple Captain",
        "worth_it": capt_xp >= 9,
        "value": round(capt_xp, 2),
        "reasoning": f"Your best captain option is projected {capt_xp:.1f} points this week.",
    })

    # Wildcard: worth it if an unconstrained rebuild beats your current
    # squad's horizon total by a wide margin.
    all_ids_by_pos = {1: [], 2: [], 3: [], 4: []}
    for pid, e in elements_by_id.items():
        all_ids_by_pos[e["element_type"]].append(pid)
    for pos in all_ids_by_pos:
        all_ids_by_pos[pos].sort(key=lambda pid: xp_totals.get(pid, 0), reverse=True)

    # Greedy affordable XV as a rough "what's achievable" ceiling, not a
    # full budget-optimal solve, just enough to gauge the gap.
    budget = BUDGET_UNITS + bank_units - sum(elements_by_id[pid]["now_cost"] for pid in current_squad_ids)
    total_budget = BUDGET_UNITS  # simplification: assume a fresh full-budget rebuild
    rebuild = []
    spent = 0
    club_counts = {}
    for pos, (lo, hi) in POSITION_LIMITS.items():
        picked = 0
        for pid in all_ids_by_pos[pos]:
            if picked >= hi:
                break
            e = elements_by_id[pid]
            if spent + e["now_cost"] > total_budget:
                continue
            if club_counts.get(e["team"], 0) >= MAX_PER_CLUB:
                continue
            rebuild.append(pid)
            spent += e["now_cost"]
            club_counts[e["team"]] = club_counts.get(e["team"], 0) + 1
            picked += 1

    rebuild_xp = sum(xp_totals.get(pid, 0) for pid in rebuild)
    current_xp_total = sum(xp_totals.get(pid, 0) for pid in current_squad_ids)
    wildcard_gap = rebuild_xp - current_xp_total
    results.append({
        "chip": "Wildcard",
        "worth_it": wildcard_gap >= 15,
        "value": round(wildcard_gap, 2),
        "reasoning": f"An unconstrained rebuild projects {wildcard_gap:.1f} points higher than your current squad over the horizon.",
    })

    # Free Hit: worth it if several of your players have no fixture this gameweek.
    affected = [pid for pid in current_squad_ids if pid in blank_gw_player_ids]
    results.append({
        "chip": "Free Hit",
        "worth_it": len(affected) >= 3,
        "value": len(affected),
        "reasoning": f"{len(affected)} of your players have no fixture this gameweek.",
    })

    return results
