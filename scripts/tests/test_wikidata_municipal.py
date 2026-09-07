import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)

import wikidata_municipal as wm  # noqa: E402


def seed(data_dir):
    """A tiny data/ca with two known people copied from the real dataset."""
    muni = os.path.join(data_dir, "ca", "municipalities")
    os.makedirs(muni)
    src = os.path.join(REPO, "data", "ca", "municipalities")
    for prefix in ("Karen-Bass-", "Alex-Vargas-"):
        fn = next(f for f in os.listdir(src) if f.startswith(prefix))
        shutil.copy(os.path.join(src, fn), os.path.join(muni, fn))
    with open(os.path.join(data_dir, "ca", "municipalities.yml"), "w") as f:
        yaml.safe_dump([
            {"name": "Los Angeles", "id": "ocd-jurisdiction/country:us/state:ca/place:los_angeles/government"},
            {"name": "Hawthorne", "id": "ocd-jurisdiction/country:us/state:ca/place:hawthorne/government"},
        ], f, sort_keys=False)


def load_all(data_dir):
    muni = os.path.join(data_dir, "ca", "municipalities")
    out = {}
    for fn in os.listdir(muni):
        with open(os.path.join(muni, fn)) as f:
            out[fn] = yaml.safe_load(f)
    return out


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        seed(self.data)
        self.fixture = os.path.join(HERE, "fixtures", "ca_sample.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_refresh(self, *extra):
        return wm.main(["--state", "ca", "--data-dir", self.data, "--fixture", self.fixture,
                        "--verified-at", "2026-09-06", *extra])

    def test_existing_mayor_is_matched_and_annotated(self):
        self.run_refresh()
        people = load_all(self.data)
        bass = next(p for p in people.values() if p["name"] == "Karen Bass")
        mayor = [r for r in bass["roles"] if r["type"] == "mayor"]
        self.assertEqual(len(mayor), 1, "must update the existing seat, not add a second")
        self.assertEqual(mayor[0]["start_date"], "2022-12-12")
        self.assertEqual(mayor[0].get("end_date"), "2026-12-11", "a known end date survives an open-ended Wikidata claim")
        self.assertIn({"scheme": "wikidata", "identifier": "Q6371239"}, bass["other_identifiers"])
        self.assertTrue(any("wikidata.org/wiki/Q6371239" in s["url"] for s in bass["sources"]))
        self.assertEqual(bass["extras"]["verified_at"], "2026-09-06")
        # Prior assembly role is preserved as history.
        self.assertTrue(any(r["type"] == "lower" for r in bass["roles"]))
        # The ended Garcetti claim is not written.
        self.assertFalse(any(p["name"] == "Eric Garcetti" for p in people.values()))

    def test_new_city_and_person_are_created(self):
        self.run_refresh()
        people = load_all(self.data)
        vaus = next(p for p in people.values() if p["name"] == "Steve Vaus")
        self.assertEqual(vaus["roles"][0]["jurisdiction"],
                         "ocd-jurisdiction/country:us/state:ca/place:poway/government")
        self.assertEqual(vaus["image"], "http://commons.example/vaus.jpg")
        self.assertEqual(vaus["given_name"], "Steve")
        with open(os.path.join(self.data, "ca", "municipalities.yml")) as f:
            munis = yaml.safe_load(f)
        self.assertIn("Poway", [m["name"] for m in munis])
        # Deterministic id: a second run reuses the same file.
        before = sorted(people)
        self.run_refresh()
        self.assertEqual(before, sorted(load_all(self.data)))

    def test_superseded_mayor_is_retired_not_deleted(self):
        # Give Alex Vargas an open-ended seat first so the retire path is exercised.
        muni = os.path.join(self.data, "ca", "municipalities")
        fn = next(f for f in os.listdir(muni) if f.startswith("Alex-Vargas"))
        with open(os.path.join(muni, fn)) as f:
            p = yaml.safe_load(f)
        p["roles"][0].pop("end_date", None)
        with open(os.path.join(muni, fn), "w") as f:
            yaml.safe_dump(p, f, sort_keys=False)

        self.run_refresh()
        people = load_all(self.data)
        vargas = next(p for p in people.values() if p["name"] == "Alex Vargas")
        self.assertIn("end_date", vargas["roles"][0])
        self.assertEqual(vargas["roles"][0]["end_reason"], "superseded per Wikidata refresh")
        new = next(p for p in people.values() if p["name"] == "New Mayor")
        self.assertEqual(new["roles"][0]["end_date"], "2028-12-31")

    def test_council_opt_in_and_no_retire_below_threshold(self):
        self.run_refresh()
        self.assertFalse(any(p["name"] == "Council Person" for p in load_all(self.data).values()))
        self.run_refresh("--include-council")
        cp = next(p for p in load_all(self.data).values() if p["name"] == "Council Person")
        self.assertEqual(cp["roles"][0]["type"], "lower")
        self.assertEqual(cp["roles"][0]["district"], "14")

    def test_dry_run_writes_nothing(self):
        before = load_all(self.data)
        self.run_refresh("--dry-run")
        self.assertEqual(before, load_all(self.data))

    def test_stale_wikidata_claim_does_not_displace_newer_incumbent(self):
        """Real-data case (Fremont, Oakland...): Wikidata's P6 still names an
        old mayor with no end date; upstream already has the new one."""
        import json
        stale = os.path.join(self.tmp, "stale.json")
        with open(stale, "w") as f:
            json.dump({"mayors": {"results": {"bindings": [
                {"city": {"value": "http://www.wikidata.org/entity/Q846914"}, "cityLabel": {"value": "Hawthorne"},
                 "person": {"value": "http://www.wikidata.org/entity/Q99999903"}, "personLabel": {"value": "Old Mayor"},
                 "start": {"value": "2016-01-01T00:00:00Z"}, "rank": {"value": "http://wikiba.se/ontology#NormalRank"}},
            ]}}}, f)
        r = wm.Refresher(self.data, "ca", dry_run=True)
        vargas = next(p for p in r.people.values() if p["name"] == "Alex Vargas")
        vargas["roles"][0]["end_date"] = "2028-12-31"  # undated start, known future term
        mayors, _ = wm.fetch("ca", False, fixture=stale)
        report = r.run(mayors, [])
        self.assertEqual(report["retired"], [])
        self.assertEqual(report["created"], [])
        self.assertEqual(len(report["conflicts"]), 1)
        self.assertEqual(report["conflicts"][0][0], "Old Mayor")

    def test_helpers(self):
        self.assertEqual(wm.name_key("Eunice M. Ulloa"), wm.name_key("Eunice Ulloa"))
        self.assertEqual(wm.name_key("James T. Butts Jr."), wm.name_key("James Butts"))
        self.assertNotEqual(wm.name_key("John Franklin"), wm.name_key("Patrick Johnson"))
        import datetime
        self.assertTrue(wm.is_current(datetime.date(2099, 1, 1)))
        self.assertFalse(wm.is_current("2001-01-01"))
        self.assertEqual(wm.slugify_place("Rancho Santa Margarita"), "rancho_santa_margarita")
        self.assertEqual(wm.slugify_place("St. Helena"), "st_helena")
        self.assertEqual(wm.clean_city_label("Poway, California"), "Poway")
        self.assertEqual(wm.ymd("2022-12-12T00:00:00Z"), "2022-12-12")

    def test_cli_runs(self):
        out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "wikidata_municipal.py"),
                              "--state", "ca", "--data-dir", self.data, "--fixture", self.fixture, "--dry-run"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("[dry-run]", out.stdout)


if __name__ == "__main__":
    unittest.main()
