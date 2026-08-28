"""
Builds the weekly report as Markdown, no email involved. It's written
to two places by main.py: a file committed into the repo under
reports/, so there's a permanent history you can browse on GitHub, and
GitHub's own Actions Step Summary, which shows up right on the
workflow run page with no extra clicking needed.
"""
import os


def build_markdown_report(context):
    """
    context is a dict assembled in main.py with everything the report
    needs: gameweek, deadline, elements_by_id, transfer_scenarios,
    starting_xi, gw_scores, chips, league_snapshots (list of
    {"name":..., "standings":[...]})
    """
    gw = context["gameweek"]
    deadline = context["deadline"]
    elements_by_id = context["elements_by_id"]
    xi = context["starting_xi"]
    gw_scores = context["gw_scores"]
    chips = context["chips"]

    def name(pid):
        return elements_by_id[pid]["web_name"]

    lines = [f"# FPL Weekly Report — Gameweek {gw}", f"**Deadline:** {deadline}", ""]

    lines.append("## Transfer Scenarios")
    lines.append("Pick whichever fits how you feel about the week, these aren't ranked, they're options.")
    lines.append("")
    scenario_labels = {0: "Hold (no transfers)", 1: "Best single move", 2: "Best double move"}
    for scenario in context["transfer_scenarios"]:
        label = scenario_labels.get(scenario["transfers_used"], f"{scenario['transfers_used']} transfers")
        lines.append(f"### {label}")
        if scenario["transfers_used"] == 0:
            lines.append(f"No changes. Squad's projected expected points over the horizon: **{scenario['new_squad_xp']:.1f}**")
        else:
            outs = ", ".join(name(p) for p in scenario["transfers_out"])
            ins = ", ".join(name(p) for p in scenario["transfers_in"])
            hit_note = f"takes a -{scenario['hit_taken']} hit" if scenario["hit_taken"] else "free transfer(s)"
            lines.append(f"**OUT:** {outs}  ")
            lines.append(f"**IN:** {ins} ({hit_note})  ")
            lines.append(f"Net change vs. holding, over the horizon: **{scenario['expected_points_gain']:+.1f} points**")
        lines.append("")
    # Note: the starting XI and captain below are chosen from your
    # CURRENT squad, not from any of the scenarios above, since which
    # transfer scenario you go with is your call. If you make a
    # transfer, next week's XI will reflect the resulting squad.

    lines.append(f"## Recommended Starting XI ({xi['formation']})")
    lines.append("| Player | Expected pts (this GW) |")
    lines.append("|---|---|")
    for pid in xi["starting_xi"]:
        tag = " (C)" if pid == xi["captain"] else (" (VC)" if pid == xi["vice_captain"] else "")
        lines.append(f"| {name(pid)}{tag} | {gw_scores.get(pid, 0):.1f} |")
    lines.append("")

    lines.append("**Bench** (in the order they'd come on):")
    lines.append("| Player | Expected pts (this GW) |")
    lines.append("|---|---|")
    for pid in xi["bench"]:
        lines.append(f"| {name(pid)} | {gw_scores.get(pid, 0):.1f} |")
    lines.append("")

    lines.append("## Chip Watch")
    any_chip_worth_it = False
    for c in chips:
        if c["worth_it"]:
            any_chip_worth_it = True
            lines.append(f"- **{c['chip']}**: {c['reasoning']}")
    if not any_chip_worth_it:
        lines.append("Nothing worth playing this week.")
    lines.append("")

    for league in context.get("league_snapshots", []):
        lines.append(f"## {league['name']}")
        lines.append("| Rank | Manager | Points |")
        lines.append("|---|---|---|")
        for i, r in enumerate(league["standings"][:8]):
            lines.append(f"| {i+1} | {r['entry_name']} | {r['total']} |")
        lines.append("")

    lines.append(
        "*Generated automatically from public FPL data. Early-season weeks "
        "carry more uncertainty than mid-season ones, as there's less history "
        "to work from.*"
    )

    return "\n".join(lines)


def write_report_files(markdown, gameweek, repo_root):
    """
    Writes the report to reports/gwN.md (for git history) and, if
    running inside GitHub Actions, appends it to the Step Summary so
    it shows up directly on the workflow run page.
    """
    reports_dir = os.path.join(repo_root, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, f"gw{gameweek}.md")
    with open(report_path, "w") as f:
        f.write(markdown)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(markdown + "\n")

    return report_path
