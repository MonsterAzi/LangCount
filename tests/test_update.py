"""Focused tests for update.py predictions and first-public-date merging.

Run with stdlib only:  python3 -m unittest discover -s tests -v
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import update


class AgeModelTest(unittest.TestCase):
    def test_t0_floored_at_github_launch(self):
        self.assertEqual(update.t0_for_year(1991), update.GITHUB_LAUNCH)
        self.assertEqual(update.t0_for_year(1972), update.GITHUB_LAUNCH)
        self.assertEqual(update.t0_for_year(None), update.GITHUB_LAUNCH)
        self.assertEqual(update.t0_for_year(2020), 2020.0)

    def test_effective_age(self):
        self.assertAlmostEqual(
            update.effective_age(1991, 2026.5), 2026.5 - update.GITHUB_LAUNCH
        )
        self.assertAlmostEqual(update.effective_age(2024, 2026.5), 2.5)
        self.assertAlmostEqual(update.effective_age(None, 2026.5), 2026.5 - 2008.25)

    def test_current_year_birth_never_divides_by_zero(self):
        self.assertGreater(update.effective_age(2026, 2026.5), 0)
        self.assertEqual(
            update.predict(1000, update.effective_age(2026, 2026.5), 1),
            round(1000 * (1 + 1 / update.effective_age(2026, 2026.5)) ** update.ALPHA),
        )

    def test_fractional_year(self):
        jan1 = update.fractional_year(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(jan1, 2026.0)
        mid = update.fractional_year(datetime(2026, 7, 2, tzinfo=timezone.utc))
        self.assertAlmostEqual(mid, 2026.5, places=2)

    def test_predict_math(self):
        # N * (1 + dt/A) ** ALPHA with A = 18.4 (uses the live constant,
        # so tuning ALPHA never breaks this test)
        self.assertEqual(
            update.predict(100_000, 18.4, 1),
            round(100_000 * (1 + 1 / 18.4) ** update.ALPHA),
        )
        self.assertEqual(
            update.predict(100_000, 18.4, 5),
            round(100_000 * (1 + 5 / 18.4) ** update.ALPHA),
        )

    def test_zero_count_stays_zero(self):
        self.assertEqual(update.predict(0, 3.0, 5), 0)

    def test_young_grows_faster_than_old(self):
        young = update.predict(10_000, 3.7, 5)
        old = update.predict(10_000, 18.4, 5)
        self.assertGreater(young, old)


class DatesCsvTest(unittest.TestCase):
    def _write_csv(self, rows):
        fh = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        )
        writer = csv.writer(fh)
        writer.writerow(["language", "first_public"])
        writer.writerows(rows)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_parses_years_and_blanks(self):
        path = self._write_csv([["Python", "1991"], ["Brainfuck", ""], ["Go", "2009"]])
        dates = update.load_first_public_years(path)
        self.assertEqual(dates, {"Python": 1991, "Brainfuck": None, "Go": 2009})

    def test_bad_year_becomes_none(self):
        path = self._write_csv([["Mystery", "not-a-year"]])
        self.assertEqual(update.load_first_public_years(path)["Mystery"], None)

    def test_missing_file_gives_empty_map(self):
        self.assertEqual(update.load_first_public_years("/nonexistent.csv"), {})


class PayloadTest(unittest.TestCase):
    def test_main_adds_dates_and_predictions(self):
        csv_path = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        )
        writer = csv.writer(csv_path)
        writer.writerow(["language", "first_public"])
        writer.writerow(["Python", "1991"])
        writer.writerow(["BrandNew", "2025"])
        csv_path.close()
        self.addCleanup(os.unlink, csv_path.name)
        out_path = csv_path.name + ".json"
        self.addCleanup(lambda: os.path.exists(out_path) and os.unlink(out_path))

        with (
            patch("update.fetch_programming_languages", return_value=["Python", "BrandNew"]),
            patch("update.fetch_repo_count", return_value=10_000),
            patch("update.time.sleep", return_value=None),
        ):
            update.main(out_path, csv_path.name)

        with open(out_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        by_lang = {item["language"]: item for item in payload["languages"]}
        self.assertEqual(by_lang["Python"]["first_public"], 1991)
        self.assertEqual(by_lang["BrandNew"]["first_public"], 2025)
        for item in payload["languages"]:
            self.assertEqual(
                set(item), {"rank", "language", "count", "first_public", "pred_1y", "pred_5y"}
            )
            self.assertGreaterEqual(item["pred_1y"], item["count"])
            self.assertGreaterEqual(item["pred_5y"], item["pred_1y"])
        # Younger language gets the larger 5-year multiple on equal counts.
        self.assertGreater(by_lang["BrandNew"]["pred_5y"], by_lang["Python"]["pred_5y"])
        self.assertIn("model", payload)
        self.assertEqual(payload["model"]["alpha"], update.ALPHA)


class RepoCsvTest(unittest.TestCase):
    """Guards the committed language_dates.csv against bad edits."""

    def test_repo_csv_is_well_formed(self):
        path = os.path.join(os.path.dirname(__file__), "..", "language_dates.csv")
        dates = update.load_first_public_years(path)
        self.assertGreater(len(dates), 500)  # tracks the full Linguist list
        now_year = 2100  # sanity upper bound only; years must be plausible
        for name, year in dates.items():
            self.assertTrue(name and name.strip(), "blank language name")
            if year is not None:
                self.assertIsInstance(year, int, name)
                self.assertGreaterEqual(year, 1945, name)
                self.assertLessEqual(year, now_year, name)


if __name__ == "__main__":
    unittest.main()
