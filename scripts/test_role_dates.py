"""Synthetic subprocess regression tests for role-date CI checks."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parent.parent / ".github/scripts/check_role_dates.py"


class RoleDateCLITests(unittest.TestCase):
    """Exercise the role-date checker through its public command-line interface."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "data"

    def write_person(self, category, filename, roles):
        """Write one synthetic YAML-compatible JSON person record."""
        path = self.data / "ca" / category / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"roles": roles}), encoding="utf-8")
        return path

    def run_checker(self, *args):
        """Run the real checker against only this test's temporary data tree."""
        # Fixed local script, interpreter, and synthetic arguments require no shell.
        return subprocess.run(  # noqa: S603
            [sys.executable, str(CHECKER), *map(str, args)],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_missing_start_date_warns_without_failing_in_both_scopes(self):
        person = self.write_person(
            "executive",
            "missing-start.yml",
            [{"type": "governor", "end_date": "2025-01-01"}],
        )

        for scope, arguments in (
            ("full", ("--data-dir", self.data)),
            ("changed", ("--data-dir", self.data, "--changed-files", person)),
        ):
            with self.subTest(scope=scope):
                result = self.run_checker(*arguments)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("warning:", result.stdout)
                self.assertIn("end_date 2025-01-01 but no start_date", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_differing_end_dates_for_same_signature_warn_without_failing(self):
        self.write_person(
            "legislature",
            "overlapping-terms.yml",
            [
                {
                    "type": "upper",
                    "jurisdiction": "ocd-jurisdiction/country:us/state:ca/government",
                    "district": "1",
                    "start_date": "2023-01-01",
                    "end_date": "2024-01-01",
                },
                {
                    "type": "upper",
                    "jurisdiction": "ocd-jurisdiction/country:us/state:ca/government",
                    "district": "1",
                    "start_date": "2023-01-01",
                    "end_date": "2025-01-01",
                },
            ],
        )

        result = self.run_checker("--data-dir", self.data)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 role entries share", result.stdout)
        self.assertIn("but have different full mappings", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_identical_full_mapping_blocks_despite_key_order_and_metadata(self):
        first = {
            "type": "lower",
            "jurisdiction": "ocd-jurisdiction/country:us/state:ca/government",
            "district": "2",
            "start_date": "2023-01-01",
            "end_date": "2025-01-01",
            "metadata": {"source": {"url": "https://example.test", "tags": ["a"]}},
        }
        second = {
            "metadata": {"source": {"tags": ["a"], "url": "https://example.test"}},
            "end_date": "2025-01-01",
            "start_date": "2023-01-01",
            "district": "2",
            "jurisdiction": "ocd-jurisdiction/country:us/state:ca/government",
            "type": "lower",
        }
        self.write_person("legislature", "duplicate.yml", [first, second])

        result = self.run_checker("--data-dir", self.data)

        self.assertEqual(result.returncode, 1)
        self.assertIn("identical full mapping to an earlier role entry", result.stdout)
        self.assertIn("Role date integrity problems found", result.stderr)

    def test_backwards_date_range_blocks_with_diagnostic(self):
        self.write_person(
            "retired",
            "backwards.yml",
            [
                {
                    "type": "upper",
                    "district": "3",
                    "start_date": "2025-01-02",
                    "end_date": "2025-01-01",
                }
            ],
        )

        result = self.run_checker("--data-dir", self.data)

        self.assertEqual(result.returncode, 1)
        self.assertIn("end_date 2025-01-01 before start_date 2025-01-02", result.stdout)
        self.assertIn("Role date integrity problems found", result.stderr)

    def test_distinct_roles_do_not_report_duplicate(self):
        self.write_person(
            "legislature",
            "distinct.yml",
            [
                {"type": "lower", "district": "1", "start_date": "2023-01-01"},
                {"type": "upper", "district": "2", "start_date": "2025-01-01"},
            ],
        )

        result = self.run_checker("--data-dir", self.data)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_changed_scope_ignores_untouched_role_errors(self):
        changed = self.write_person(
            "legislature",
            "changed.yml",
            [{"type": "lower", "district": "1", "start_date": "2023-01-01"}],
        )
        self.write_person(
            "retired",
            "untouched.yml",
            [
                {
                    "type": "upper",
                    "district": "2",
                    "start_date": "2025-01-02",
                    "end_date": "2025-01-01",
                }
            ],
        )

        result = self.run_checker("--data-dir", self.data, "--changed-files", changed)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("before start_date", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_changed_scope_skips_nonperson_and_deleted_paths(self):
        nonperson = self.data / "ca" / "committees" / "committee.yml"
        nonperson.parent.mkdir(parents=True)
        nonperson.write_text("{}", encoding="utf-8")
        deleted = self.data / "ca" / "retired" / "deleted.yml"

        for changed_files in ((nonperson,), (deleted,)):
            with self.subTest(changed_files=changed_files):
                result = self.run_checker(
                    "--data-dir", self.data, "--changed-files", *changed_files
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "No changed person files to check\n")
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
