"""Convert People source records without inferring election method or incumbency.

Normalization is pure and accepts an explicit reference date. The CLI retains
its existing output layout; publication/rollback belongs to the backend.
"""

import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Direct build invocations must not create __pycache__ in a read-only checkout.
if __name__ == "__main__":
    sys.dont_write_bytecode = True

from converter_safety import check_output_tree, check_path, load_yaml, output_file

CALENDAR_DATE_LENGTH = 10


def load_source_yaml(path):
    """Read YAML safely without consulting or creating a source-side pickle."""
    return load_yaml(path)


def jurisdiction_level(jurisdiction):
    """Classify OCD segments, never the role title or office mailing address."""
    segments = (jurisdiction or "").split("/")
    if any(s.startswith("place:") for s in segments):
        return "city"
    if any(s.startswith("county:") for s in segments):
        return "county"
    if any(
        s.startswith(("district:", "school_district:", "township:")) for s in segments
    ):
        return "local"
    # Unknown sub-state segments must not silently become statewide seats.
    known = ("ocd-jurisdiction", "government", "country:us")
    if any(s not in known and not s.startswith("state:") for s in segments):
        return "unknown"
    if any(s.startswith("state:") for s in segments):
        return "state"
    return "national" if "country:us" in segments else "unknown"


def normalized_role(raw_type, level):
    """Return an additive role key; retain unrecognized source values verbatim."""
    key = "".join(c for c in raw_type.lower() if c.isalpha())
    aliases = {
        "ltgovernor": "lieutenant_governor",
        "lieutenantgovernor": "lieutenant_governor",
        "attorneygeneral": "attorney_general",
        "secretaryofstate": "secretary_of_state",
        "chiefelectionofficer": "chief_election_officer",
        "governor": "governor",
        "mayor": "mayor",
        "council": "council",
        "councilmember": "council",
    }
    if key in ("upper", "lower") and level in ("city", "county", "local"):
        return "council"
    return aliases.get(key, raw_type)


def parsed_date(value):
    """Parse only calendar YYYY-MM-DD values; missing/invalid values return None."""
    if not isinstance(value, str) or len(value) != CALENDAR_DATE_LENGTH:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def term_status(term, as_of):
    """Classify a term without turning missing dates into proof of incumbency."""
    start, end = parsed_date(term.get("start")), parsed_date(term.get("end"))
    if any(
        term.get(k) not in (None, "") and parsed_date(term[k]) is None
        for k in ("start", "end")
    ):
        return "ambiguous"
    if start and end and end < start:
        return "ambiguous"
    if start and start > as_of:
        return "future"
    if end and end < as_of:
        return "historical"
    marker = term.get("source_current")
    if marker is False:
        return "historical"
    return "current" if marker is True or start else "ambiguous"


def order_terms(terms, as_of):
    """Copy/classify all terms and put one deterministic display choice last.

    Current beats ambiguous, historical, then future. Selection is not evidence
    of incumbency and does not discard concurrent offices or historical terms.
    """
    annotated = []
    for term in terms:
        status = term_status(term, as_of)
        annotated.append(
            dict(
                term,
                term_status=status,
                current=None if status == "ambiguous" else status == "current",
                is_display_term=False,
            )
        )
    if not annotated:
        return annotated

    def preference(term):
        status = term["term_status"]
        rank = {"future": 0, "historical": 1, "ambiguous": 2, "current": 3}[status]
        start = parsed_date(term.get("start")) or date.min
        end = parsed_date(term.get("end")) or date.min
        if status == "future":
            return rank, -start.toordinal(), -end.toordinal()
        if status == "historical":
            return rank, end.toordinal(), start.toordinal()
        return rank, start.toordinal(), end.toordinal()

    # max is stable: equally ranked offices choose the first source role.
    selected = max(range(len(annotated)), key=lambda i: preference(annotated[i]))
    display = annotated.pop(selected)
    annotated.sort(
        key=lambda t: (
            parsed_date(t.get("start")) or date.min,
            parsed_date(t.get("end")) or date.min,
        )
    )
    display["is_display_term"] = True
    return [*annotated, display]


def contact_fields(data, address_parser):
    """Retain both office shapes and expose non-fabricated contact fallbacks."""
    offices = list(data.get("offices") or [])
    for office in data.get("contact_details") or []:
        if office not in offices:
            offices.append(office)
    fields = {"addresses": [address_parser(office) for office in offices]}
    email = data.get("email") or next(
        (o["email"] for o in offices if o.get("email")), None
    )
    phone = data.get("phone") or next(
        (o["voice"] for o in offices if o.get("voice")), None
    )
    if email:
        fields["email"] = email
        # Existing Flutter interpretation remains supported.
        fields["contact_form"] = email
    if data.get("contact_form"):
        fields["web_form"] = data["contact_form"]
        fields.setdefault("contact_form", data["contact_form"])
    if phone:
        fields["phone"] = phone
    links = [dict(link) for link in data.get("links") or []]
    if links:
        fields["links"] = links
        urls = [link["url"] for link in links if link.get("url")]
        if urls:
            fields["url"] = urls[-1]  # Preserve legacy website preference.
    return fields


def convert_term(role, data, state_name, place_names, address_parser):
    """Preserve the source type and add independently interpretable metadata."""
    raw_type = role["type"]
    jurisdiction = role.get("jurisdiction")
    level = jurisdiction_level(jurisdiction)
    term = dict(
        contact_fields(data, address_parser),
        type=raw_type,
        raw_type=raw_type,
        role_type=normalized_role(raw_type, level),
        state=state_name,
        jurisdiction_level=level,
        selection_method="unknown",
    )
    for old, new in (
        ("district", "district"),
        ("title", "title"),
        ("end_reason", "end_reason"),
        ("current", "source_current"),
    ):
        if old in role:
            term[new] = role[old]
    for old, new in (("start_date", "start"), ("end_date", "end")):
        if role.get(old) not in (None, ""):
            term[new] = str(role[old])
    if jurisdiction is not None:
        term["jurisdiction"] = jurisdiction
        term["place"] = jurisdiction_place_name(jurisdiction, place_names)
    if "selection_method" in role:
        method = role["selection_method"]
        term["selection_method_raw"] = method
        if isinstance(method, str) and method.strip().lower() in (
            "elected",
            "appointed",
        ):
            term["selection_method"] = method.strip().lower()
    if data.get("party"):
        term["party"] = data["party"][0].get("name")
    if data.get("sources"):
        term["sources"] = [s["url"] for s in data["sources"] if s.get("url")]
    extras = data.get("extras") or {}
    if extras.get("verified_at"):
        term["verified_at"] = str(extras["verified_at"])
    return term


def convert_person(data, state_name, place_names, as_of, address_parser):
    """Convert one record, preserving IDs and all roles without mutating input."""
    person = {"id": {}, "name": {}, "bio": {}, "term_status_as_of": as_of.isoformat()}
    if "id" in data:
        person["id"]["openstates"] = data["id"]
    for ident in data.get("other_identifiers") or []:
        if ident.get("scheme") == "wikidata":
            person["id"]["wikidata"] = ident.get("identifier")
    for old, new in (
        ("name", "official_full"),
        ("given_name", "first"),
        ("family_name", "last"),
    ):
        if old in data:
            person["name"][new] = data[old]
    if "gender" in data:
        person["bio"]["gender"] = {"Male": "M", "Female": "F"}.get(
            data["gender"], data["gender"]
        )
    if data.get("birth_date"):
        person["bio"]["birthday"] = str(data["birth_date"])
    if "image" in data:
        person["image_url"] = data["image"]
    terms = [
        convert_term(role, data, state_name, place_names, address_parser)
        for role in data.get("roles") or []
    ]
    person["terms"] = order_terms(terms, as_of)
    return person


# Keep the public signature and independently injectable offline dependencies.
def generate_legislator_json(  # noqa: PLR0913
    data_dir=None,
    output_dir=None,
    *,
    force_refresh=False,
    as_of=None,
    loader=None,
    state_names=None,
    address_parser=None,
):
    """Generate sorted .yml/.yaml inputs; injectable edges keep tests offline.

    This writes individual files, NOT an atomic dataset. Backend callers must
    use a staging destination before validating and publishing the whole tree.
    """
    if loader is None:
        loader = load_source_yaml
    if state_names is None:
        # Injected callers need not import the optional default catalog module.
        from state_names import states  # noqa: PLC0415

        state_names = states
    # Preserve lexical normalization without resolving a symlinked script path.
    script_dir = Path(os.path.abspath(__file__)).parent  # noqa: PTH100
    data_dir = Path(data_dir or script_dir / "../data")
    output_dir = Path(output_dir or script_dir / "../alternate_formats/json")
    check_output_tree(output_dir)
    # The documented standalone CLI supports Python 3.9, before datetime.UTC.
    as_of = as_of or datetime.now(timezone.utc).date()  # noqa: UP017
    address_parser = address_parser or parse_office_address
    for state_dir in sorted(data_dir.iterdir()):
        state = state_dir.name
        if state == "us" or not state_dir.is_dir():
            continue
        place_names = load_place_names(state_dir, loader)
        for source_dir in sorted(state_dir.iterdir()):
            if not source_dir.is_dir():
                continue
            destination = output_dir / state / (source_dir.name + ".json")
            check_path(destination)
            if not force_refresh and destination.exists():
                continue
            # Retain glob's hidden-file and full-pattern matching semantics,
            # including string paths for existing injected loaders.
            paths = sorted(
                [
                    *glob.glob(str(source_dir / "*.yml")),  # noqa: PTH207
                    *glob.glob(str(source_dir / "*.yaml")),  # noqa: PTH207
                ]
            )
            records = [
                convert_person(
                    loader(path),
                    state_names[state.upper()],
                    place_names,
                    as_of,
                    address_parser,
                )
                for path in paths
            ]
            with output_file(destination) as output:
                json.dump(records, output, indent=2)


def load_place_names(state_dir, loader):
    """Read both catalog extensions; reject conflicting names or malformed input."""
    names = {}
    for extension in ("yml", "yaml"):
        path = Path(state_dir) / ("municipalities." + extension)
        if not path.exists():
            continue
        # As with person files, retain string arguments for injected loaders.
        for entry in loader(str(path)) or []:
            identity, name = entry["id"], entry["name"]
            if identity in names and names[identity] != name:
                # Preserve the existing exception type and caller-visible message.
                raise ValueError(  # noqa: TRY003
                    "Conflicting jurisdiction display names"
                )
            names[identity] = name
    return names


def jurisdiction_place_name(jurisdiction, place_names):
    """Display name for an OCD jurisdiction id.

    Prefers the catalog; otherwise uses a place or county segment. A county
    display name is not a city; consumers must also read jurisdiction_level.
    """
    if jurisdiction in place_names:
        return place_names[jurisdiction]
    for prefix in ("place:", "county:"):
        for segment in jurisdiction.split("/"):
            if segment.startswith(prefix):
                return segment[len(prefix) :].replace("_", " ").title()
    return None


def parse_office_address(office, parser=None):
    """Parse an office while retaining source contact values and raw address."""
    if parser is None:
        # Native parsing stays optional for injected parsers and offline imports.
        # Optional runtime dependency is absent from the lint environment.
        import postal.parser  # noqa: PLC0415  # ty: ignore[unresolved-import]

        parser = postal.parser.parse_address

    # Run libpostal; it always returns lower-cased components.
    parsed_items = parser(office.get("address") or "")

    # Restore capitalisation, e.g. "pennsylvania" becomes "Pennsylvania".
    parsed_items = [(component.title(), label) for component, label in parsed_items]

    # parse_address() returns a list of (component, label) tuples,
    # turn it into a dict keyed by label for easier lookup
    comps = {label: component for component, label in parsed_items}

    # First line: house number + road (+ road type if present).
    addr1_parts = [
        comps[key] for key in ("house_number", "road", "road_type") if key in comps
    ]
    address1 = " ".join(addr1_parts) if addr1_parts else None

    # Second line: retain component order and include present-but-empty values.
    secondary = [
        prefix + comps[key]
        for key, prefix in (
            ("room", "Room "),
            ("unit", ""),
            ("level", "Level "),
            ("suite", "Suite "),
            ("po_box", "P.O. Box "),
        )
        if key in comps
    ]
    address2 = ", ".join(secondary) if secondary else None
    new_office = {}
    new_office["address1"] = address1
    new_office["address2"] = address2
    new_office["city"] = comps.get("city")
    new_office["state"] = comps.get("state")
    new_office["zip"] = comps.get("postcode")

    if "voice" in office:
        new_office["phone"] = office["voice"]
    if "classification" in office:
        new_office["title"] = office["classification"]
    elif "note" in office:
        new_office["title"] = office["note"]
    for key in ("email", "fax"):
        if office.get(key):
            new_office[key] = office[key]
    if office.get("address"):
        new_office["raw_address"] = office["address"]

    return new_office


def reference_date(value):
    """Accept only an explicit YYYY-MM-DD calendar day at the CLI boundary."""
    parsed = parsed_date(value)
    if parsed is None or parsed.isoformat() != value:
        # argparse callers rely on this exception type and diagnostic text.
        raise argparse.ArgumentTypeError(  # noqa: TRY003
            "expected a YYYY-MM-DD calendar date"
        )
    return parsed


def main(argv=None):
    """Generate into an explicit JSON root; defaults retain the legacy layout."""
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=root / "data",
        help="People data root containing state directories",
    )
    parser.add_argument(
        "--output-dir", type=Path, help="JSON destination root (not its parent)"
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="overwrite existing destination files",
    )
    parser.add_argument(
        "--as-of",
        type=reference_date,
        help="UTC reference date, YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--remove-pickles",
        action="store_true",
        help="legacy maintenance only: delete source *.pickle files",
    )
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error("--input-dir must be an existing directory")
    if args.remove_pickles:
        if args.output_dir is not None or args.force_refresh or args.as_of is not None:
            parser.error("--remove-pickles cannot be combined with generation options")
        for path in args.input_dir.rglob("*.pickle"):
            path.unlink()
        return
    output_dir = args.output_dir or root / "alternate_formats" / "json"
    if output_dir.resolve().is_relative_to(args.input_dir.resolve()):
        parser.error("--output-dir must be outside --input-dir")
    generate_legislator_json(
        data_dir=args.input_dir,
        output_dir=output_dir,
        force_refresh=args.force_refresh,
        as_of=args.as_of,
    )


if __name__ == "__main__":
    main()
