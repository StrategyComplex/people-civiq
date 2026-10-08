#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import yaml

_PERSON_DIRS = ("executive", "legislature", "municipalities", "retired")
_PERSON_PATH_DEPTH = 3


def normalize(value: object) -> str:
    """Normalize names for duplicate comparisons."""
    return " ".join(str(value or "").casefold().split())


def person_files(data_dir: Path, state: str) -> list[Path]:
    """Return all person YAML files for a state, excluding committees."""
    state_dir = data_dir / state
    files: list[Path] = []
    for person_dir in _PERSON_DIRS:
        directory = state_dir / person_dir
        if directory.exists():
            files.extend(sorted(directory.glob("*.yml")))
            files.extend(sorted(directory.glob("*.yaml")))
    return files


def person_file_state(data_dir: Path, path: Path) -> str | None:
    """Return a changed person file's state, or None when the path should be ignored."""
    try:
        parts = path.resolve().relative_to(data_dir.resolve()).parts
    except ValueError:
        return None

    if len(parts) != _PERSON_PATH_DEPTH:
        return None
    if parts[1] not in _PERSON_DIRS:
        return None
    if path.suffix not in (".yml", ".yaml"):
        return None
    return parts[0]


def duplicate_groups(
    data_dir: Path, state: str
) -> tuple[dict[tuple[str, str], list[Path]], dict[str, list[Path]]]:
    """Find normalized-name and exact-ID duplicate groups for one state."""
    names: dict[tuple[str, str], list[Path]] = defaultdict(list)
    ids: dict[str, list[Path]] = defaultdict(list)

    for path in person_files(data_dir, state):
        with path.open() as file:
            record = yaml.safe_load(file) or {}

        given_name = normalize(record.get("given_name"))
        family_name = normalize(record.get("family_name"))
        if given_name and family_name:
            names[(given_name, family_name)].append(path.resolve())

        person_id = record.get("id")
        if isinstance(person_id, str) and person_id:
            ids[person_id].append(path.resolve())

    name_duplicates = {
        key: paths for key, paths in sorted(names.items()) if len(paths) > 1
    }
    id_duplicates = {key: paths for key, paths in sorted(ids.items()) if len(paths) > 1}
    return name_duplicates, id_duplicates


def report_name_duplicates(
    state: str,
    duplicates: dict[tuple[str, str], list[Path]],
    changed_paths: set[Path] | None,
) -> None:
    """Print name-only duplicate groups as review warnings."""
    for (given_name, family_name), paths in duplicates.items():
        if changed_paths is not None and not changed_paths.intersection(paths):
            continue
        print(
            f"WARNING: {state}: possible duplicate person records for "
            f"given_name={given_name!r}, family_name={family_name!r}"
        )
        for path in paths:
            marker = (
                " (changed)"
                if changed_paths is not None and path in changed_paths
                else ""
            )
            print(f"  - {path}{marker}")


def report_id_duplicates(
    state: str,
    duplicates: dict[str, list[Path]],
    changed_paths: set[Path] | None,
) -> bool:
    """Print exact-ID duplicate groups and report whether any were in scope."""
    found_duplicates = False
    for person_id, paths in duplicates.items():
        if changed_paths is not None and not changed_paths.intersection(paths):
            continue
        found_duplicates = True
        print(f"ERROR: {state}: duplicate person id={person_id!r}")
        for path in paths:
            marker = (
                " (changed)"
                if changed_paths is not None and path in changed_paths
                else ""
            )
            print(f"  - {path}{marker}")
    return found_duplicates


def changed_person_files(
    data_dir: Path, changed_files: list[str]
) -> dict[str, set[Path]]:
    """Group changed person files by state for CI-scoped duplicate checks."""
    changed_by_state: dict[str, set[Path]] = defaultdict(set)
    for changed_file in changed_files:
        path = Path(changed_file).resolve()
        state = person_file_state(data_dir, path)
        if state is None or not path.exists():
            continue
        changed_by_state[state].add(path)
    return changed_by_state


def main() -> int:
    """Run either a full-state duplicate scan or a changed-file-scoped CI scan."""
    parser = argparse.ArgumentParser(
        description=(
            "Warn about normalized-name matches and fail on exact duplicate "
            "person IDs within the same state."
        )
    )
    parser.add_argument(
        "states", nargs="*", help="state abbreviations to check, e.g. ar ma"
    )
    parser.add_argument("--data-dir", default="data", type=Path)
    parser.add_argument(
        "--changed-files",
        nargs="*",
        help=(
            "limit failures to duplicate groups containing one of these "
            "changed person files"
        ),
    )
    args = parser.parse_args()

    found_duplicate_ids = False
    changed_by_state = changed_person_files(args.data_dir, args.changed_files or [])

    if args.changed_files is not None:
        if not changed_by_state:
            print("No changed person files to check")
            return 0

        # Existing historical duplicate groups are allowed unless this change
        # touches one of them.
        for state, changed_paths in sorted(changed_by_state.items()):
            name_duplicates, id_duplicates = duplicate_groups(args.data_dir, state)
            report_name_duplicates(state, name_duplicates, changed_paths)
            found_duplicate_ids |= report_id_duplicates(
                state, id_duplicates, changed_paths
            )
    else:
        if not args.states:
            parser.error("provide states to check, or use --changed-files")

        for state in sorted(set(args.states)):
            name_duplicates, id_duplicates = duplicate_groups(args.data_dir, state)
            report_name_duplicates(state, name_duplicates, None)
            found_duplicate_ids |= report_id_duplicates(state, id_duplicates, None)

    if found_duplicate_ids:
        print(
            "\nDuplicate person IDs found. In CI, only duplicate groups containing "
            "changed person files fail.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
