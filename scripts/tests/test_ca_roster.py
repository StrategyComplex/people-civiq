#!/usr/bin/env python3
"""Tests for scripts/ca_roster.py."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ca_roster as cr  # noqa: E402

# A small fixture mimicking the roster's layout: a normal entry, a vacant
# mayor, a vice-mayor label variant, and an entry that spans a page marker.
FIXTURE = """\
=== PAGE 1 ===
Incorporated Cities and Town Officials
Please Note: The data herein is provided to the Secretary of State's Office.
City of Alpha
( County of Example )
Address: 1 Main St, Alpha, CA 90001
Telephone: (555) 111-2222
Website: www.alpha.example
Email: clerk@alpha.example
Office Hours: M-F 8-5
Mayor: Jane Smith
Vice Mayor/Mayor Pro Tempore: John Doe
City Council: Alice Adams
Bob Brown
Carol Clark
1st and 3rd Tuesdays at 7 pm
City Manager: Pat Manager
City Clerk: Sam Clerk
City Attorney: Lee Attorney
Incorporated: 1/1/1900
Legislative Districts: AD: 1 CD: 1 SD: 1
Chartered City Population: 1000
City of Beta
( County of Example )
Address: 2 Main St, Beta, CA 90002
Telephone: (555) 333-4444
Website: www.beta.example
Email: clerk@beta.example
Office Hours: M-F 8-5
Mayor: Vacant
Vice Mayor/Mayor Pro Tempore: Nancy Nolan
City Council: Owen Otto, Peggy Price
2nd and 4th Mondays at 6 pm
City Manager: Pat Manager
Incorporated: 2/2/1901
Legislative Districts: AD: 2 CD: 2 SD: 2
Chartered City Population: 2000

=== PAGE 2 ===
Incorporated Cities and Town Officials
City of Gamma
( County of Example )
Address: 3 Main St, Gamma, CA 90003
Telephone: (555) 555-6666
Website: www.gamma.example
Email: clerk@gamma.example
Office Hours: M-F 8-5
Mayor: Gary Green
Mayor Pro Tem: Helen Hill
City Council: Ivan Ives
Judy Jones
Every Wednesday at 5 pm
City Manager: Pat Manager
Incorporated: 3/3/1902
Legislative Districts: AD: 3 CD: 3 SD: 3
Chartered City Population: 3000
City of Delta
( County of Example )
Address: 4 Main St, Delta, CA 90004
Telephone: (555) 777-8888
Website: www.delta.example
Email: clerk@delta.example
Office Hours: M-F 8-5
Mayor: Diana Dale
Vice Mayor/Mayor Pro Tempore: Frank Ford
City Council: Grace Gold
Henry Hall
Some meeting note continues here
1st Thursday of the month at 6 pm
City Manager: Pat Manager
Incorporated: 4/4/1903
Legislative Districts: AD: 4 CD: 4 SD: 4
Chartered City Population: 4000
"""


class CaRosterTest(unittest.TestCase):
    def setUp(self):
        self.cities = {c["city"]: c for c in cr.parse_roster_text(FIXTURE)}

    def test_all_cities_found(self):
        self.assertEqual(set(self.cities), {"Alpha", "Beta", "Gamma", "Delta"})

    def test_normal_entry(self):
        alpha = self.cities["Alpha"]
        self.assertEqual(alpha["county"], "Example")
        self.assertEqual(alpha["mayor"], {"name": "Jane Smith"})
        self.assertEqual(
            alpha["council"],
            [
                {"name": "John Doe", "note": "Vice Mayor/Mayor Pro Tempore"},
                {"name": "Alice Adams"},
                {"name": "Bob Brown"},
                {"name": "Carol Clark"},
            ],
        )
        # the meeting-schedule line must not leak into the council list
        names = [p["name"] for p in alpha["council"]]
        self.assertNotIn("1st and 3rd Tuesdays at 7 pm", names)

    def test_vacant_mayor_is_omitted(self):
        beta = self.cities["Beta"]
        self.assertNotIn("mayor", beta)
        self.assertEqual(
            beta["council"],
            [
                {"name": "Nancy Nolan", "note": "Vice Mayor/Mayor Pro Tempore"},
                {"name": "Owen Otto"},
                {"name": "Peggy Price"},
            ],
        )

    def test_vice_mayor_label_variant(self):
        gamma = self.cities["Gamma"]
        self.assertEqual(gamma["mayor"], {"name": "Gary Green"})
        self.assertEqual(
            gamma["council"],
            [
                {"name": "Helen Hill", "note": "Mayor Pro Tem"},
                {"name": "Ivan Ives"},
                {"name": "Judy Jones"},
            ],
        )

    def test_entry_spanning_page_marker(self):
        # Delta's block sits right after "=== PAGE 2 ===" and its council
        # list ends with a schedule line one line before the true trailer.
        delta = self.cities["Delta"]
        self.assertEqual(delta["mayor"], {"name": "Diana Dale"})
        self.assertEqual(
            delta["council"],
            [
                {"name": "Frank Ford", "note": "Vice Mayor/Mayor Pro Tempore"},
                {"name": "Grace Gold"},
                {"name": "Henry Hall"},
            ],
        )

    def test_main_writes_expected_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_path = os.path.join(tmp, "roster.txt")
            out_path = os.path.join(tmp, "out.json")
            with open(text_path, "w") as f:
                f.write(FIXTURE)
            cr.main(["--text", text_path, "-o", out_path, "--verified-at", "2026-05-01"])
            with open(out_path) as f:
                doc = json.load(f)
        self.assertEqual(doc["verified_at"], "2026-05-01")
        self.assertEqual(doc["source"], cr.SOURCE_NAME)
        self.assertEqual(len(doc["cities"]), 4)

    def test_exclude_filters_cities(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_path = os.path.join(tmp, "roster.txt")
            excl_path = os.path.join(tmp, "excl.json")
            out_path = os.path.join(tmp, "out.json")
            with open(text_path, "w") as f:
                f.write(FIXTURE)
            with open(excl_path, "w") as f:
                json.dump({"cities": [{"city": "Beta"}, {"city": "Delta"}]}, f)
            cr.main(["--text", text_path, "--exclude", excl_path, "-o", out_path])
            with open(out_path) as f:
                doc = json.load(f)
        self.assertEqual({c["city"] for c in doc["cities"]}, {"Alpha", "Gamma"})


if __name__ == "__main__":
    unittest.main()
