#!/usr/bin/env python3
"""Refresh municipal office holders (mayors, optionally council members) from Wikidata.

Wikidata models a city's mayor as `head of government` (P6) with start/end
qualifiers, and council seats as `position held` (P39) on the person. Both
are CC0, so the data can be redistributed with attribution to the entity URL.

Usage:
    python3 wikidata_municipal.py --state ca              # write YAML
    python3 wikidata_municipal.py --state ca --dry-run    # report only
    python3 wikidata_municipal.py --state ca --include-council
    python3 wikidata_municipal.py --state ca --fixture tests/fixtures/ca.json

`--fixture` feeds saved SPARQL JSON instead of hitting the network, which is
also how the tests run. Every written record carries
`other_identifiers: [{scheme: wikidata, identifier: Q...}]`,
`sources: [{url: https://www.wikidata.org/wiki/Q..., note: ...}]` and
`extras.verified_at`, so downstream can show provenance and freshness.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import uuid

import yaml

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "civiq-people-refresh/1.0 (https://github.com/StrategyComplex/people-civiq)"

# Wikidata entity ids for US states (the P131* anchor). Extend as states ship.
STATE_ENTITY = {
    "ca": "Q99",
}

# Namespace for deterministic ocd-person ids derived from a Wikidata QID, so a
# re-run never creates a second file for the same person.
_PERSON_NS = uuid.UUID("6f1c1a7e-3b9f-4a3b-9b58-1d5f1e0f9c11")

# Classes a "city" may be an instance of on Wikidata. P279* subclass walks are
# too slow at state scale, so enumerate the common concrete classes instead.
CITY_CLASSES = [
    "Q515",  # city
    "Q3957",  # town
    "Q1093829",  # city in the United States
    "Q15127012",  # town in the United States
    "Q3327873",  # charter city (California)
    "Q1549591",  # big city
    "Q62049",  # county seat
    "Q47133724",  # city of California (if present)
]

MAYOR_QUERY = """
SELECT ?city ?cityLabel ?gnis ?person ?personLabel ?start ?end ?partyLabel ?image ?website ?rank
WHERE {
  VALUES ?cls { %(classes)s }
  ?city wdt:P31 ?cls ;
        wdt:P131* wd:%(state)s .
  ?city p:P6 ?st .
  ?st ps:P6 ?person ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL { ?st pq:P580 ?start . }
  OPTIONAL { ?st pq:P582 ?end . }
  OPTIONAL { ?city wdt:P590 ?gnis . }
  OPTIONAL { ?city wdt:P856 ?website . }
  OPTIONAL { ?person wdt:P102 ?party . }
  OPTIONAL { ?person wdt:P18 ?image . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""

COUNCIL_QUERY = """
SELECT ?city ?cityLabel ?person ?personLabel ?start ?end ?districtLabel ?partyLabel ?image ?rank
WHERE {
  VALUES ?cls { %(classes)s }
  ?city wdt:P31 ?cls ;
        wdt:P131* wd:%(state)s .
  ?pos wdt:P1001 ?city ;
       wdt:P279* wd:Q708492 .          # city councillor
  ?person p:P39 ?st .
  ?st ps:P39 ?pos ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL { ?st pq:P580 ?start . }
  OPTIONAL { ?st pq:P582 ?end . }
  OPTIONAL { ?st pq:P768 ?district . }
  OPTIONAL { ?person wdt:P102 ?party . }
  OPTIONAL { ?person wdt:P18 ?image . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


# --------------------------------------------------------------------------- helpers
def slugify_place(name):
    """'Rancho Santa Margarita' -> 'rancho_santa_margarita' (OCD place slug)."""
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s


def jurisdiction_id(state, place_slug):
    return "ocd-jurisdiction/country:us/state:%s/place:%s/government" % (state, place_slug)


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def ymd(value):
    """Wikidata datetimes are '2022-12-12T00:00:00Z'; keep the date part."""
    if not value:
        return None
    return value[:10]


def today():
    return dt.date.today().isoformat()


def is_current(end):
    return end is None or end >= today()


def normalize_name(name):
    return re.sub(r"[^a-z]", "", name.lower())


def clean_city_label(label):
    """Wikidata labels like 'Los Angeles' are fine; strip ', California' suffixes."""
    return re.sub(r",\s*California$", "", label).strip()


# --------------------------------------------------------------------------- SPARQL
def run_query(query):
    import requests  # imported lazily so tests with --fixture need no network

    resp = requests.get(
        SPARQL_ENDPOINT,
        params={"query": query, "format": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def bindings(results):
    for row in results["results"]["bindings"]:
        yield {k: v.get("value") for k, v in row.items()}


def fetch(state, include_council, fixture=None):
    """Returns (mayor_rows, council_rows) as lists of plain dicts."""
    if fixture:
        with open(fixture) as f:
            data = json.load(f)
        council = data.get("council", {"results": {"bindings": []}}) if include_council else {"results": {"bindings": []}}
        return list(bindings(data["mayors"])), list(bindings(council))
    params = {"classes": " ".join("wd:" + c for c in CITY_CLASSES), "state": STATE_ENTITY[state]}
    mayors = list(bindings(run_query(MAYOR_QUERY % params)))
    council = list(bindings(run_query(COUNCIL_QUERY % params))) if include_council else []
    return mayors, council


# --------------------------------------------------------------------------- selection
def current_office_holders(rows, key="person"):
    """Collapse SPARQL rows (one per optional combination) into the current
    holder(s) per city: statements without an end date or ending today or
    later, preferring PreferredRank, then the latest start."""
    by_city = {}
    for r in rows:
        city = r["city"]
        rec = by_city.setdefault(city, {})
        person = r[key]
        entry = rec.setdefault(
            person,
            {
                "city": city,
                "city_label": clean_city_label(r.get("cityLabel", "")),
                "gnis": r.get("gnis"),
                "website": r.get("website"),
                "person": person,
                "name": r.get("personLabel", ""),
                "start": ymd(r.get("start")),
                "end": ymd(r.get("end")),
                "party": None,
                "image": None,
                "district": r.get("districtLabel"),
                "preferred": r.get("rank", "").endswith("PreferredRank"),
            },
        )
        # Multiple rows per statement come from OPTIONAL joins; keep first
        # party/image seen and the widest date range.
        entry["party"] = entry["party"] or r.get("partyLabel")
        entry["image"] = entry["image"] or r.get("image")
        if r.get("start") and (entry["start"] is None or ymd(r["start"]) < entry["start"]):
            entry["start"] = ymd(r["start"])
        if r.get("end") and (entry["end"] is None or ymd(r["end"]) > entry["end"]):
            entry["end"] = ymd(r["end"])

    current = {}
    for city, people in by_city.items():
        live = [p for p in people.values() if is_current(p["end"])]
        if not live:
            continue
        live.sort(key=lambda p: (p["preferred"], p["start"] or ""), reverse=True)
        current[city] = live
    return current


# --------------------------------------------------------------------------- YAML I/O
KEY_ORDER = [
    "id", "name", "given_name", "family_name", "middle_name", "suffix", "gender",
    "email", "biography", "birth_date", "death_date", "image", "ids", "party",
    "roles", "offices", "links", "other_identifiers", "other_names", "sources", "extras",
]


def ordered(person):
    out = {k: person[k] for k in KEY_ORDER if k in person}
    out.update({k: v for k, v in person.items() if k not in out})
    return out


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def dump_yaml(person, path):
    with open(path, "w") as f:
        yaml.safe_dump(ordered(person), f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def person_filename(name, person_id):
    base = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    return "%s-%s.yml" % (base, person_id.split("/")[-1])


def split_name(full):
    parts = full.split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return full, ""


# --------------------------------------------------------------------------- merge
class Refresher:
    def __init__(self, data_dir, state, dry_run=False, verified_at=None):
        self.state = state
        self.dry_run = dry_run
        self.verified_at = verified_at or today()
        self.state_dir = os.path.join(data_dir, state)
        self.muni_dir = os.path.join(self.state_dir, "municipalities")
        self.muni_list_path = os.path.join(self.state_dir, "municipalities.yml")
        self.municipalities = load_yaml(self.muni_list_path) if os.path.exists(self.muni_list_path) else []
        self.name_to_jid = {normalize_name(m["name"]): m["id"] for m in self.municipalities}
        self.people = {}  # path -> person dict
        if os.path.isdir(self.muni_dir):
            for fn in os.listdir(self.muni_dir):
                if fn.endswith(".yml"):
                    self.people[os.path.join(self.muni_dir, fn)] = load_yaml(os.path.join(self.muni_dir, fn))
        self.report = {"created": [], "updated": [], "retired": [], "new_municipalities": [], "skipped": []}

    # ---- lookup
    def jurisdiction_for(self, city_label):
        jid = self.name_to_jid.get(normalize_name(city_label))
        if jid:
            return jid, False
        jid = jurisdiction_id(self.state, slugify_place(city_label))
        self.municipalities.append({"name": city_label, "id": jid})
        self.name_to_jid[normalize_name(city_label)] = jid
        self.report["new_municipalities"].append(city_label)
        return jid, True

    def find_person(self, wikidata_qid, name, jid):
        for path, p in self.people.items():
            for ident in p.get("other_identifiers", []) or []:
                if ident.get("scheme") == "wikidata" and ident.get("identifier") == wikidata_qid:
                    return path, p
        for path, p in self.people.items():
            if normalize_name(p.get("name", "")) == normalize_name(name) and any(
                r.get("jurisdiction") == jid for r in p.get("roles", []) or []
            ):
                return path, p
        return None, None

    # ---- apply
    def apply(self, holder, role_type, jid):
        q = qid(holder["person"])
        path, person = self.find_person(q, holder["name"], jid)
        new_role = {"type": role_type, "jurisdiction": jid}
        if holder["start"]:
            new_role["start_date"] = holder["start"]
        if holder["end"]:
            new_role["end_date"] = holder["end"]
        if role_type != "mayor" and holder.get("district"):
            new_role["district"] = holder["district"]

        if person is None:
            given, family = split_name(holder["name"])
            pid = "ocd-person/%s" % uuid.uuid5(_PERSON_NS, q)
            person = {"id": pid, "name": holder["name"], "given_name": given, "family_name": family, "roles": [new_role]}
            path = os.path.join(self.muni_dir, person_filename(holder["name"], pid))
            self.people[path] = person
            self.report["created"].append((holder["name"], role_type, holder["city_label"]))
        else:
            roles = person.setdefault("roles", [])
            same = [r for r in roles if r.get("type") == role_type and r.get("jurisdiction") == jid]
            if same:
                # Refresh dates on the existing seat; never drop history.
                same[-1].update(new_role)
                same[-1].pop("end_date", None) if not holder["end"] else None
            else:
                roles.append(new_role)
            self.report["updated"].append((holder["name"], role_type, holder["city_label"]))

        if holder.get("party") and not person.get("party"):
            person["party"] = [{"name": holder["party"]}]
        if holder.get("image") and not person.get("image"):
            person["image"] = holder["image"]
        idents = person.setdefault("other_identifiers", [])
        if not any(i.get("scheme") == "wikidata" for i in idents):
            idents.append({"scheme": "wikidata", "identifier": q})
        sources = person.setdefault("sources", [])
        wd_url = "https://www.wikidata.org/wiki/" + q
        if not any(s.get("url") == wd_url for s in sources):
            sources.append({"url": wd_url, "note": "Wikidata %s" % ("head of government (P6)" if role_type == "mayor" else "position held (P39)")})
        if holder.get("website") and not person.get("links"):
            person["links"] = [{"url": holder["website"], "note": "city website"}]
        extras = person.setdefault("extras", {})
        extras["verified_at"] = self.verified_at
        extras["verified_by"] = "wikidata"
        return path

    def retire_others(self, jid, role_type, keep_paths):
        """Close out a seat's previous holder(s): anyone else with an open or
        future-dated role of this type in this jurisdiction gets end_date =
        today. Their file is kept (history), the app just stops listing them."""
        for path, p in self.people.items():
            if path in keep_paths:
                continue
            for r in p.get("roles", []) or []:
                if r.get("type") == role_type and r.get("jurisdiction") == jid and is_current(r.get("end_date")):
                    r["end_date"] = today()
                    r["end_reason"] = "superseded per Wikidata refresh"
                    self.report["retired"].append((p.get("name"), role_type, jid))

    def run(self, mayors, council):
        for city, holders in current_office_holders(mayors).items():
            holder = holders[0]  # a city has one mayor; extra rows are stale claims
            if not holder["city_label"] or not holder["name"]:
                self.report["skipped"].append(city)
                continue
            jid, _ = self.jurisdiction_for(holder["city_label"])
            path = self.apply(holder, "mayor", jid)
            self.retire_others(jid, "mayor", {path})
        for city, holders in current_office_holders(council).items():
            if not holders[0]["city_label"]:
                continue
            jid, _ = self.jurisdiction_for(holders[0]["city_label"])
            kept = set()
            for h in holders:
                if h["name"]:
                    kept.add(self.apply(h, "lower", jid))
            # Council rosters are partial on Wikidata; only retire when we
            # positively know the new roster (>= 3 members) to avoid wiping
            # a full council because Wikidata lists one member.
            if len(kept) >= 3:
                self.retire_others(jid, "lower", kept)
        return self.report

    def write(self):
        if self.dry_run:
            return
        os.makedirs(self.muni_dir, exist_ok=True)
        for path, person in self.people.items():
            dump_yaml(person, path)
        with open(self.muni_list_path, "w") as f:
            yaml.safe_dump(self.municipalities, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


def print_report(report, dry_run):
    tag = "[dry-run] " if dry_run else ""
    print("%screated %d, updated %d, retired %d, new municipalities %d, skipped %d" % (
        tag, len(report["created"]), len(report["updated"]), len(report["retired"]),
        len(report["new_municipalities"]), len(report["skipped"])))
    for name, role, city in report["created"]:
        print("  + %s (%s, %s)" % (name, role, city))
    for name, role, jid in report["retired"]:
        print("  - retired %s (%s, %s)" % (name, role, jid))
    for city in report["new_municipalities"]:
        print("  * new municipality %s" % city)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True, choices=sorted(STATE_ENTITY))
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-council", action="store_true")
    ap.add_argument("--fixture", help="saved SPARQL JSON ({'mayors': ..., 'council': ...}) instead of the network")
    ap.add_argument("--verified-at", help="override the verified_at date (tests)")
    args = ap.parse_args(argv)

    mayors, council = fetch(args.state, args.include_council, args.fixture)
    refresher = Refresher(args.data_dir, args.state, dry_run=args.dry_run, verified_at=args.verified_at)
    report = refresher.run(mayors, council)
    refresher.write()
    print_report(report, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
