"""Fetch GitHub repository counts for every Linguist programming language.

Reads the language list live from Linguist's ``languages.yml``, queries the
GitHub Search API once per programming language, and writes the ranked
results to ``data.json`` for the static frontend.

Usage:
    GITHUB_TOKEN=ghp_... python3 update.py [output_path]

Environment:
    GITHUB_TOKEN: optional but strongly recommended. Authenticated search
        requests get 30 req/min instead of 10 req/min unauthenticated.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
import yaml

LANGUAGES_YML_URL = (
    "https://raw.githubusercontent.com/github-linguist/linguist/main"
    "/lib/linguist/languages.yml"
)
SEARCH_API_URL = "https://api.github.com/search/repositories"

# Stay safely below the 30 req/min authenticated Search rate limit.
MIN_REQUEST_INTERVAL = 2.1
# Extra buffer added after a rate-limit reset window before retrying.
RATE_LIMIT_BUFFER = 3.0
MAX_RETRIES = 5
REQUEST_TIMEOUT = 30
OUTPUT_PATH = "data.json"
DATES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "language_dates.csv")

# Prediction model: N_future = N_now * (1 + dt/A) ** ALPHA,
# where A = t_now - t_0 is the language's effective age in years on GitHub
# and t_0 = max(first_public_year, GITHUB_LAUNCH). Repos can only accumulate
# since GitHub exists, so pre-2008 languages all count from launch.
ALPHA = 3.0
PREDICT_HORIZONS = (1, 5)  # years ahead; rendered as pred_1y / pred_5y
GITHUB_LAUNCH = 2008 + (4 - 1) / 12  # April 2008, i.e. 2008.25
MIN_EFFECTIVE_AGE = 1 / 12  # one month; guards against division by zero


def _sleep_until_reset(response: requests.Response) -> float:
    """Return seconds to sleep based on rate-limit headers (min 0)."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after)) + RATE_LIMIT_BUFFER
        except ValueError:
            pass
    reset = response.headers.get("X-RateLimit-Reset")
    if reset is not None:
        try:
            wait = float(reset) - time.time()
            return max(0.0, wait) + RATE_LIMIT_BUFFER
        except ValueError:
            pass
    # Fallback: wait a full minute plus buffer.
    return 60.0 + RATE_LIMIT_BUFFER


def _get_with_retry(
    session: requests.Session, url: str, **kwargs
) -> requests.Response:
    """GET ``url`` with retries for rate limits and transient failures."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2.0 * attempt)  # exponential-ish backoff: 2,4,6,8s
            continue
        if response.status_code == 200:
            return response
        if response.status_code in (403, 429):
            # Rate limited: sleep until the window resets, then retry.
            wait = _sleep_until_reset(response)
            reset_at = time.strftime("%H:%M:%S UTC", time.gmtime(time.time() + wait))
            print(
                f"Rate limited (HTTP {response.status_code}), "
                f"sleeping {wait:.0f}s until ~{reset_at} "
                f"(attempt {attempt}/{MAX_RETRIES})...",
                flush=True,
            )
            time.sleep(wait)
            last_error = requests.HTTPError(
                f"Rate limited: HTTP {response.status_code} for {url}"
            )
            continue
        if response.status_code >= 500 and attempt < MAX_RETRIES:
            time.sleep(2.0 * attempt)
            last_error = requests.HTTPError(
                f"HTTP {response.status_code} for {url}: {response.text[:200]}"
            )
            continue
        # Other 4xx errors are not retryable (bad query, auth, etc.).
        response.raise_for_status()
    raise RuntimeError(f"Failed to GET {url} after {MAX_RETRIES} attempts") from last_error


def fractional_year(moment: datetime) -> float:
    """Convert a timestamp to a fractional year, e.g. mid-2026 -> ~2026.5."""
    year_start = datetime(moment.year, 1, 1, tzinfo=timezone.utc)
    next_year = datetime(moment.year + 1, 1, 1, tzinfo=timezone.utc)
    return moment.year + (moment - year_start) / (next_year - year_start)


def t0_for_year(first_public: int | None) -> float:
    """Effective start year: birth year, floored at the GitHub launch."""
    if first_public is None:
        return GITHUB_LAUNCH
    return max(float(first_public), GITHUB_LAUNCH)


def effective_age(first_public: int | None, now: float) -> float:
    """A = t_now - t_0 in years (floored at one month, never zero)."""
    return max(now - t0_for_year(first_public), MIN_EFFECTIVE_AGE)


def predict(count: int, age_years: float, years: int) -> int:
    """Extrapolate a repo count: N_now * (1 + dt/A) ** ALPHA."""
    return int(round(count * (1 + years / age_years) ** ALPHA))


def load_first_public_years(path: str) -> dict[str, int | None]:
    """Load {language: first-public year or None} from the dates CSV."""
    dates: dict[str, int | None] = {}
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("language") or "").strip()
                if not name:
                    continue
                raw = (row.get("first_public") or "").strip()
                try:
                    dates[name] = int(raw) if raw else None
                except ValueError:
                    print(f"Warning: bad year {raw!r} for {name}; treating as unknown.")
                    dates[name] = None
    except OSError as exc:
        print(f"Warning: could not read {path} ({exc}); dates will be unknown.")
    return dates


def fetch_programming_languages(session: requests.Session) -> list[str]:
    """Return sorted names of all ``type == "programming"`` languages."""
    print(f"Fetching {LANGUAGES_YML_URL} ...", flush=True)
    response = _get_with_retry(session, LANGUAGES_YML_URL)
    response.raise_for_status()
    data = yaml.safe_load(response.text)
    languages = [
        name
        for name, meta in data.items()
        if isinstance(meta, dict) and meta.get("type") == "programming"
    ]
    languages.sort()
    print(f"Found {len(languages)} programming languages.", flush=True)
    return languages


def fetch_repo_count(session: requests.Session, language: str) -> int:
    """Return the Search API ``total_count`` for one language.

    The query is passed via ``params`` so ``requests`` URL-encodes names
    containing spaces or symbols (``C++``, ``C#``, ``F#``, ``Common Lisp``).
    """
    # ``per_page=1`` keeps payloads tiny; ``total_count`` is unaffected.
    response = _get_with_retry(
        session,
        SEARCH_API_URL,
        params={"q": f'language:"{language}"', "per_page": 1},
    )
    return int(response.json().get("total_count", 0))


def main(output_path: str = OUTPUT_PATH, dates_path: str = DATES_CSV) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "langcount-dashboard/1.0",
        }
    )
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    else:
        print(
            "Warning: GITHUB_TOKEN not set; using unauthenticated requests "
            "(10 req/min). Set GITHUB_TOKEN for the 30 req/min limit.",
            flush=True,
        )

    languages = fetch_programming_languages(session)
    first_public = load_first_public_years(dates_path)
    now_frac = fractional_year(datetime.now(timezone.utc))
    missing = [lang for lang in languages if first_public.get(lang) is None]
    if missing:
        print(
            f"{len(missing)} languages have no first-public year "
            f"(showing blank, using GitHub launch as t_0).",
            flush=True,
        )

    results: list[dict] = []
    last_request = 0.0
    total = len(languages)
    for i, language in enumerate(languages, 1):
        # Enforce minimum spacing between Search requests.
        elapsed = time.time() - last_request
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        try:
            count = fetch_repo_count(session, language)
        except Exception as exc:  # noqa: BLE001 - record and continue
            print(f"[{i}/{total}] {language}: ERROR ({exc}), recording 0", flush=True)
            count = 0
        last_request = time.time()
        year = first_public.get(language)
        age = effective_age(year, now_frac)
        results.append(
            {
                "language": language,
                "count": count,
                "first_public": year,
                "pred_1y": predict(count, age, PREDICT_HORIZONS[0]),
                "pred_5y": predict(count, age, PREDICT_HORIZONS[1]),
            }
        )
        print(f"[{i}/{total}] {language}: {count:,}", flush=True)

    results.sort(key=lambda item: item["count"], reverse=True)
    payload = {
        "last_updated": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "total_languages": len(results),
        "model": {
            "formula": "N_future = N_now * (1 + dt/A) ** alpha",
            "age_definition": "A = t_now - t_0 in years",
            "t0_definition": "max(first_public_year, 2008.25 [GitHub launch, April 2008])",
            "alpha": ALPHA,
            "horizons_years": list(PREDICT_HORIZONS),
        },
        "languages": [
            {
                "rank": rank,
                "language": item["language"],
                "count": item["count"],
                "first_public": item["first_public"],
                "pred_1y": item["pred_1y"],
                "pred_5y": item["pred_5y"],
            }
            for rank, item in enumerate(results, 1)
        ],
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Wrote {output_path} with {len(results)} languages.", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else OUTPUT_PATH)
