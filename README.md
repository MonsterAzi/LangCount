# LangCount

Static dashboard showing GitHub repository counts for every programming
language recognized by [GitHub Linguist](https://github.com/github-linguist/linguist).
A scheduled GitHub Action refreshes the data daily; the frontend is
dependency-free HTML/CSS/JS hosted on GitHub Pages.

- `update.py` — fetches `languages.yml`, queries the Search API once per
  programming language (~2.1 s spacing, header-aware rate-limit retries),
  writes ranked results to `data.json`.
- `.github/workflows/update.yml` — daily 00:00 UTC run plus manual dispatch.
- `index.html` — renders `data.json` with search, sorting, and dark/light mode.
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

## Local development

```bash
pip install requests pyyaml
GITHUB_TOKEN=ghp_... python update.py   # writes data.json
python -m http.server 8000              # open http://localhost:8000
```
