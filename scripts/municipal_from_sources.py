#!/usr/bin/env python3
"""Apply a hand/agent-collected roster of mayors and council members, taken
from each city's official website, to data/<state>/municipalities.

Input JSON (see data/sources/municipal/<state>/*.json):

  {"verified_at": "2026-09-06", "cities": [
     {"city": "Poway", "source_url": "https://poway.org/163/City-Council",
      "mayor": {"name": "Steve Vaus", "end_date": "2026-12-01"},
      "council": [{"name": "Jenny Maeda", "district": "District 4",
                   "end_date": "2028-12-01", "note": "Deputy Mayor"}, ...]}]}

An official city page is authoritative: it replaces whatever the file holds
for that seat (previous holders are end-dated today, files kept). Mayors are
`type: mayor`; council members are `type: lower` with `district`. Records are
matched by first+last name within the jurisdiction, so re-runs update in
place. Each touched person gets `sources` (the page), `extras.verified_at`
and `extras.verified_by: city-website`.

Usage:
  python3 scripts/municipal_from_sources.py --state ca --input data/sources/municipal/ca/san-diego-county.json [--dry-run]
"""
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikidata_municipal as wm  # noqa: E402

_NS = uuid.UUID("2c0b7d6e-8f0a-4c1d-9a51-3b0d2f6e7a11")


class SourceRefresher(wm.Refresher):
    def __init__(self, data_dir, state, dry_run=False, verified_at=None, verified_by="city-website"):
        super().__init__(data_dir, state, dry_run=dry_run, verified_at=verified_at)
        self.verified_by = verified_by
        self.report["conflicts"] = []

    def apply_seat(self, seat, role_type, jid, city_label, source_url):
        name = seat["name"].strip()
        path, person = self.find_person(None, name, jid)
        role = {"type": role_type, "jurisdiction": jid}
        if seat.get("start_date"):
            role["start_date"] = seat["start_date"]
        if seat.get("end_date"):
            role["end_date"] = seat["end_date"]
        if role_type != "mayor" and seat.get("district"):
            role["district"] = seat["district"]
        if person is None:
            given, family = wm.split_name(name)
            pid = "ocd-person/%s" % uuid.uuid5(_NS, jid + "|" + wm.name_key(name))
            person = {"id": pid, "name": name, "given_name": given, "family_name": family, "roles": [role]}
            path = os.path.join(self.muni_dir, wm.person_filename(name, pid))
            self.people[path] = person
            self.report["created"].append((name, role_type, city_label))
        else:
            roles = person.setdefault("roles", [])
            same = [r for r in roles if r.get("type") == role_type and r.get("jurisdiction") == jid]
            if same:
                cur = same[-1]
                cur_end = wm.datestr(cur.get("end_date"))
                cur.update(role)
                # The source confirms the seat is held today. Keep a known future
                # end date it doesn't mention; only a past one is stale.
                if not seat.get("end_date") and cur_end and cur_end < wm.today():
                    cur.pop("end_date", None)
                cur.pop("end_reason", None)
            else:
                roles.append(role)
            self.report["updated"].append((name, role_type, city_label))
        if seat.get("note"):
            person.setdefault("extras", {})["title"] = seat["note"]
        sources = person.setdefault("sources", [])
        if not any(s.get("url") == source_url for s in sources):
            sources.append({"url": source_url, "note": "official city website"})
        extras = person.setdefault("extras", {})
        extras["verified_at"] = self.verified_at
        extras["verified_by"] = self.verified_by
        return path

    def run_sources(self, doc):
        for c in doc["cities"]:
            jid, _ = self.jurisdiction_for(c["city"])
            src = c["source_url"]
            if c.get("mayor"):
                path = self.apply_seat(c["mayor"], "mayor", jid, c["city"], c.get("mayor_source_url", src))
                self.retire_others(jid, "mayor", {path})
            kept = set()
            for seat in c.get("council") or []:
                kept.add(self.apply_seat(seat, "lower", jid, c["city"], src))
            if kept:
                self.retire_others(jid, "lower", kept)
        return self.report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verified-by", default="city-website")
    args = ap.parse_args(argv)
    with open(args.input) as f:
        doc = json.load(f)
    r = SourceRefresher(args.data_dir, args.state, dry_run=args.dry_run, verified_at=doc.get("verified_at"),
                         verified_by=args.verified_by)
    report = r.run_sources(doc)
    r.write()
    wm.print_report(report, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
