"""Synthetic subprocess regression tests for duplicate-person CI checks."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = (
    Path(__file__).resolve().parent.parent / ".github/scripts/check_duplicate_people.py"
)


class DuplicatePeopleCLITests(unittest.TestCase):
    """Exercise the checker through its public command-line interface."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "data"

    def write_person(self, state, category, filename, **record):
        """Write one synthetic YAML-compatible JSON person record."""
        path = self.data / state / category / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
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

    def test_normalized_name_collision_warns_without_failing(self):
        self.write_person(
            "ca",
            "executive",
            "one.yml",
            id="one",
            given_name=" Ada ",
            family_name="LOVELACE",
        )
        self.write_person(
            "ca",
            "legislature",
            "two.yaml",
            id="two",
            given_name="ada",
            family_name="lovelace",
        )

        result = self.run_checker("--data-dir", self.data, "ca")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: ca: possible duplicate person records", result.stdout)
        self.assertNotIn("ERROR:", result.stdout)

    def test_exact_ids_block_even_when_names_differ_or_are_absent(self):
        self.write_person(
            "ca",
            "executive",
            "named-one.yml",
            id="shared-name-id",
            given_name="Ada",
            family_name="Lovelace",
        )
        self.write_person(
            "ca",
            "legislature",
            "named-two.yml",
            id="shared-name-id",
            given_name="Grace",
            family_name="Hopper",
        )
        self.write_person("ca", "municipalities", "unnamed-one.yml", id="unnamed-id")
        self.write_person("ca", "retired", "unnamed-two.yml", id="unnamed-id")

        result = self.run_checker("--data-dir", self.data, "ca")

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: ca: duplicate person id='shared-name-id'", result.stdout)
        self.assertIn("ERROR: ca: duplicate person id='unnamed-id'", result.stdout)
        self.assertIn("Duplicate person IDs found", result.stderr)

    def test_changed_scope_includes_counterpart_but_not_historical_group(self):
        changed = self.write_person("ca", "executive", "changed.yml", id="touched-id")
        self.write_person("ca", "legislature", "counterpart.yml", id="touched-id")
        self.write_person("ca", "municipalities", "historic-one.yml", id="historic-id")
        self.write_person("ca", "retired", "historic-two.yml", id="historic-id")

        result = self.run_checker("--data-dir", self.data, "--changed-files", changed)

        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate person id='touched-id'", result.stdout)
        self.assertIn("counterpart.yml", result.stdout)
        self.assertIn("changed.yml (changed)", result.stdout)
        self.assertNotIn("historic-id", result.stdout)

    def test_empty_nonperson_and_deleted_changed_paths_skip_checks(self):
        self.write_person("ca", "executive", "one.yml", id="duplicate-id")
        self.write_person("ca", "legislature", "two.yml", id="duplicate-id")
        nonperson = self.data / "ca" / "committees" / "committee.yml"
        nonperson.parent.mkdir(parents=True)
        nonperson.write_text("{}", encoding="utf-8")
        deleted = self.data / "ca" / "retired" / "deleted.yml"

        for changed_files in ((), (nonperson,), (deleted,)):
            with self.subTest(changed_files=changed_files):
                result = self.run_checker(
                    "--data-dir", self.data, "--changed-files", *changed_files
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "No changed person files to check\n")

    def test_untouched_duplicate_ids_do_not_fail_unique_changed_person(self):
        changed = self.write_person("ca", "executive", "unique.yml", id="unique")
        self.write_person("ca", "legislature", "old-one.yml", id="historical")
        self.write_person("ca", "retired", "old-two.yml", id="historical")

        result = self.run_checker("--changed-files", changed)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("historical", result.stdout)
        self.assertNotIn("ERROR:", result.stdout)

    def test_changed_name_collision_warns_but_untouched_names_do_not(self):
        changed = self.write_person(
            "ca",
            "executive",
            "changed.yml",
            id="one",
            given_name="Ada",
            family_name="Lovelace",
        )
        self.write_person(
            "ca",
            "legislature",
            "counterpart.yml",
            id="two",
            given_name="Ada",
            family_name="Lovelace",
        )
        for filename in ("old-one.yml", "old-two.yml"):
            self.write_person(
                "ca",
                "retired",
                filename,
                id=filename,
                given_name="Grace",
                family_name="Hopper",
            )

        result = self.run_checker("--changed-files", changed)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: ca:", result.stdout)
        self.assertIn("changed.yml (changed)", result.stdout)
        self.assertIn("counterpart.yml", result.stdout)
        self.assertNotIn("hopper", result.stdout)
        self.assertNotIn("ERROR:", result.stdout)

    def test_relative_and_absolute_data_and_changed_paths_match(self):
        changed = self.write_person("ca", "executive", "changed.yml", id="shared-id")
        self.write_person("ca", "legislature", "counterpart.yml", id="shared-id")

        cases = (
            (Path("data"), changed.relative_to(self.root)),
            (self.data, changed),
            (Path("data"), changed),
            (self.data, changed.relative_to(self.root)),
        )
        for data_dir, changed_path in cases:
            with self.subTest(data_dir=data_dir, changed_path=changed_path):
                result = self.run_checker(
                    "--data-dir", data_dir, "--changed-files", changed_path
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("duplicate person id='shared-id'", result.stdout)
                self.assertIn("changed.yml (changed)", result.stdout)

    def test_full_state_mode_checks_duplicate_ids(self):
        self.write_person("ca", "executive", "one.yml", id="full-state-id")
        self.write_person("ca", "retired", "two.yml", id="full-state-id")

        result = self.run_checker("--data-dir", self.data, "ca")

        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate person id='full-state-id'", result.stdout)
        self.assertNotIn("(changed)", result.stdout)

    def test_case_distinct_and_missing_ids_are_not_duplicate_groups(self):
        self.write_person("ca", "executive", "upper.yml", id="CaseSensitive")
        self.write_person("ca", "legislature", "lower.yml", id="casesensitive")
        self.write_person("ca", "municipalities", "missing-one.yml")
        self.write_person("ca", "retired", "missing-two.yml")

        result = self.run_checker("--data-dir", self.data, "ca")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ERROR:", result.stdout)


if __name__ == "__main__":
    unittest.main()
