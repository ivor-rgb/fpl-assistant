# FPL Assistant

Automated weekly Fantasy Premier League transfer, team, and chip recommendations
for your team (Colsinho FC). Each week's report is committed straight into this
repo under `reports/`, and also shows up directly on the GitHub Actions run
page, no email involved. History is tracked in Supabase.

This was built and tested against the live FPL API. Two real bugs (small-sample
noise inflating both team and player ratings early in the season) were found
and fixed during testing, see the comments in `team_strength.py` and
`scoring.py` if you're curious how.

## What it does each week

1. Pulls your current squad, all player data, fixtures, and your two mini-leagues
2. Scores every player's expected points from recent underlying stats (xG, xA,
   defensive contribution, clean sheet probability), not just past points
3. Works out the best possible starting XI, captain, and transfer(s) for the
   upcoming gameweek using a proper MILP solver (PuLP)
4. Checks whether Bench Boost, Triple Captain, Wildcard, or Free Hit look
   worth playing this week
5. Writes the report to `reports/gwN.md` and pushes it, plus shows it on the
   Actions run summary page
6. Saves the recommendation and predictions to Supabase, so accuracy can be
   checked over the season

## One-time setup

### 1. Push this to your own GitHub repo

```
cd fpl-assistant
git init
git add .
git commit -m "Initial commit"
```

Create a new repository on GitHub (it can be private), then:

```
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

### 2. Let Actions push commits

The workflow needs permission to push the weekly report back into your repo.
In your repo: Settings > Actions > General > Workflow permissions, select
**"Read and write permissions"**, then save.

### 3. Create a free Supabase project

Go to [supabase.com](https://supabase.com), create a free project, then in the
SQL editor paste and run the contents of `sql/schema.sql` in this repo. That
creates the four tables the tool uses.

From your Supabase project's Settings > API page, copy:
- The **Project URL**
- The **service_role** key (not the anon key, the tool needs write access)

### 4. Add two secrets to your GitHub repo

In your repo: Settings > Secrets and variables > Actions > New repository secret.
Add:

| Secret name | Value |
|---|---|
| `SUPABASE_URL` | your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | your Supabase service_role key |

### 5. Enable Actions

Go to the Actions tab in your repo and enable workflows if prompted. That's it,
it'll now run automatically every day at 08:00 UTC, and only actually generate
a report when a deadline is within 60 hours.

### 6. Test it manually

Go to Actions > FPL Weekly Report > Run workflow, tick "force", and run it.
Once it finishes, check the run's summary page, and check that a new file
appeared under `reports/` in your repo. This is the best way to confirm
everything's wired up correctly before waiting for the real schedule.

## Updating your free transfers count

The public FPL API doesn't expose "free transfers currently available"
directly, only your season-long transfer total, and working it out properly
means replicating FPL's rollover and wildcard rules from your full transfer
history. Rather than guess, edit `settings.json` and update the
`free_transfers` number yourself each week, it's shown on the FPL site's
Transfers page. If you forget, it defaults to 1, which is wrong more often
than it's right if you've been banking transfers.

## Known limitations, worth knowing about

- **Early season noise.** With only a gameweek or two of real data, every
  player's "recent form" is really just their most recent match or two. The
  model applies shrinkage toward sensible averages to stop one freak result
  skewing things, but it will still sharpen up as the season goes on.
- **This week's decision, not a season-long plan.** The transfer optimiser
  picks the best move for the upcoming gameweek using multi-week expected
  value, it doesn't plan several future weeks of transfers as one combined
  strategy the way some of the more involved community tools do (see
  `optimizer.py` for the reasoning).
- **Chip timing is a comparison, not a guarantee.** It flags when a chip's
  expected value clears a threshold based on the same numbers as everything
  else, it isn't jointly optimised with the transfer decision in a single
  solve.
- **Candidate pool, not the entire player database.** The transfer search
  restricts itself to each position's top 30 players by expected points to
  keep the solve fast, in practice a player outside that range was never
  going to be the right transfer target anyway.
