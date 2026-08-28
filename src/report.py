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
    needs: gameweek, deadline, elements_by_id, transfer, starting_xi,
    gw_scores, chips, league_snapshots (list of {"name":..., "standings":[...]})
    """
    gw = context["gameweek"]
    deadline = context["deadline"]
    elements_by_id = context["elements_by_id"]
    transfer = context["transfer"]
    xi = context["starting_xi"]
    gw_scores = context["gw_scores"]
    chips = context["chips"]

    def name(pid):
        return elements_by_id[pid]["web_name"]

    lines = [f"# FPL Weekly Report — Gameweek {gw}", f"**Deadline:** {deadline}", ""]

    lines.append("## Recommended Transfer")
    if transfer["transfers_used"] == 0:
        lines.append("No transfer clears the bar this week, holding is the recommendation.")
    else:
        outs = ", ".join(name(p) for p in transfer["transfers_out"])
        ins = ", ".join(name(p) for p in transfer["transfers_in"])
        hit_note = f"takes a -{transfer['hit_taken']} hit" if transfer["hit_taken"] else "free transfer"
        lines.append(f"**OUT:** {outs}  ")
        lines.append(f"**IN:** {ins} ({hit_note})  ")
        lines.append(f"Projected net gain over the horizon: **{transfer['expected_points_gain']:.1f} points**")
    lines.append("")

    lines.append(f"## Recommended Starting XI ({xi['formation']})")
    lines.append("| Player | Expected pts (this GW) |")
    lines.append("|---|---|")
    for pid in xi["starting_xi"]:
        tag = " (C)" if pid == xi["captain"] else (" (VC)" if pid == xi["vice_captain"] else "")
        lines.append(f"| {name(pid)}{tag} | {gw_scores.get(pid, 0):.1f} |")
    lines.append("")
    bench_names = ", ".join(name(pid) for pid in xi["bench"])
    lines.append(f"**Bench:** {bench_names}")
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
