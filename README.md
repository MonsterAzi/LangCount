# LangCount

Static dashboard showing GitHub repository counts for every programming
language recognized by [GitHub Linguist](https://github.com/github-linguist/linguist).
A scheduled GitHub Action refreshes the data daily; the frontend is
dependency-free HTML/CSS/JS hosted on GitHub Pages.

- `update.py` — fetches `languages.yml`, queries the Search API once per
  programming language (~2.1 s spacing, header-aware rate-limit retries),
  merges first-public years, computes 1yr/5yr projections, and writes
  ranked results to `data.json`.
- `language_dates.csv` — first-public year (first public compiler or
  interpreter) per language, curated by hand; blank where unknown.
  Edit this file to fix or fill in dates — no code changes needed.
- `.github/workflows/update.yml` — daily 00:00 UTC run plus manual dispatch
  (runs unit tests first, then refreshes `data.json`).
- `index.html` — renders `data.json` with search, click-to-sort column
  headers, and dark/light mode.
- `tests/test_update.py` — stdlib-only unit tests (`python -m unittest
  discover -s tests -v`).
- `data.json` — generated artifact (committed by the workflow).

## Setup

1. Push these files to a repository whose default branch is `main`.
2. Enable Pages: **Settings > Pages > Build and deployment**,
   source **Deploy from a branch**, branch **`main`**, folder **`/ (root)`**.
3. Allow the workflow to commit: **Settings > Actions > General >
   Workflow permissions** → **"Read and write permissions"**,
   then **Save**.
4. Install the Python dependencies for local runs:
   `pip install requests pyyaml`.
5. Trigger the first run: **Actions > Update language counts >
   Run workflow** (`workflow_dispatch`). This populates `data.json`;
   the run takes roughly 16 minutes for ~450 languages.

## First-public dates

`language_dates.csv` has two columns, `language,first_public`, with one row
per Linguist programming language (year granularity; blank = unknown, shown
as "—"). `update.py` loads it relative to its own location, warns about rows
it cannot parse, and carries on with unknown dates. To correct a date, just
edit the CSV and re-run (or wait for the next daily run).

## Prediction model

Each language gets `pred_1y` / `pred_5y` projections from

`N_future = N_now * (1 + Δt/A) ** alpha`, with `alpha = 1.3`.

`A = t_now − t_0` is the language's effective age in years on GitHub, where
`t_0 = max(first_public_year, April 2008)` — repo counts can only accumulate
since GitHub exists, so a 1972 language and a 2008 language start from the
same launch date. Languages with an unknown birth year use the April 2008
launch as `t_0`. Smaller `A` (younger languages) yields steeper projected
growth. These are rough extrapolations, not measurements — tune `ALPHA` and
`GITHUB_LAUNCH` at the top of `update.py`. The active parameters are also
recorded in the `model` block of every generated `data.json`.

## Local development

```bash
pip install requests pyyaml
python -m unittest discover -s tests -v   # stdlib only, no extra deps
GITHUB_TOKEN=ghp_... python update.py      # writes data.json
python -m http.server 8000                 # open http://localhost:8000
```
