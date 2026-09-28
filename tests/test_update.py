"""Focused tests for update.py predictions and first-public-date merging.

Run with stdlib only:  python3 -m unittest discover -s tests -v
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import update


class PredictTest(unittest.TestCase):
    def test_one_year_factor(self):
        # (1 + 0.10*1) ** 1.3 ~= 1.1319
        self.assertEqual(update.predict(100_000, 0.10, 1), round(100_000 * 1.1**1.3))

    def test_five_year_factor(self):
        # (1 + 0.10*5) ** 1.3 = 1.5 ** 1.3 ~= 1.6941
        self.assertEqual(update.predict(100_000, 0.10, 5), round(100_000 * 1.5**1.3))

    def test_zero_count_stays_zero(self):
        self.assertEqual(update.predict(0, 0.30, 5), 0)

    def test_young_grows_faster_than_old(self):
        young = update.predict(10_000, update.growth_rate_for_age(2), 5)
        old = update.predict(10_000, update.growth_rate_for_age(40), 5)
        self.assertGreater(young, old)


class GrowthRateTest(unittest.TestCase):
    def test_brackets(self):
        self.assertEqual(update.growth_rate_for_age(2), 0.30)
        self.assertEqual(update.growth_rate_for_age(5), 0.20)
        self.assertEqual(update.growth_rate_for_age(10), 0.20)
        self.assertEqual(update.growth_rate_for_age(11), 0.10)
        self.assertEqual(update.growth_rate_for_age(20), 0.10)
        self.assertEqual(update.growth_rate_for_age(21), 0.05)
        self.assertEqual(update.growth_rate_for_age(60), 0.05)

    def test_unknown_age_gets_default(self):
        self.assertEqual(update.growth_rate_for_age(None), update.DEFAULT_RATE)

    def test_year_mapping(self):
        now = 2026
        self.assertEqual(
            update.growth_rate_for_year(2024, now), update.growth_rate_for_age(2)
        )
        self.assertEqual(update.growth_rate_for_year(None, now), update.DEFAULT_RATE)


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
