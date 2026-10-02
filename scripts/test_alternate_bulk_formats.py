"""Offline contract tests; every record and generated file is synthetic."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import alternate_bulk_formats as converter


AS_OF = date(2030, 6, 15)
STATE = 'ocd-jurisdiction/country:us/state:xx/government'
COUNTY = 'ocd-jurisdiction/country:us/state:xx/county:example/government'
CITY = 'ocd-jurisdiction/country:us/state:xx/place:example/government'


def person(roles):
    """Return a minimal invented source person, never a production record."""
    return {'id': 'synthetic-person-1', 'name': 'Example Official',
            'given_name': 'Example', 'family_name': 'Official', 'roles': roles}


def convert(data):
    """Avoid native address parsing in pure normalization tests."""
    return converter.convert_person(data, 'Example State', {}, AS_OF, dict)


class NormalizationTests(unittest.TestCase):
    def test_role_aliases_are_additive_and_unknown_roles_survive(self):
        for raw, expected in [('lt_governor', 'lieutenant_governor'),
                              ('Lieutenant Governor', 'lieutenant_governor'),
                              ('attorney general', 'attorney_general'),
                              ('attorneyGeneral', 'attorney_general'),
                              ('Custom Board Seat', 'Custom Board Seat')]:
            with self.subTest(raw=raw):
                term = convert(person([{'type': raw, 'jurisdiction': STATE}]))['terms'][0]
                self.assertEqual((term['type'], term['raw_type'], term['role_type']),
                                 (raw, raw, expected))
                self.assertEqual(term['selection_method'], 'unknown')

    def test_jurisdiction_not_title_controls_level_and_council(self):
        for jurisdiction, level in [(CITY, 'city'), (COUNTY, 'county'), (STATE, 'state'),
                                     (None, 'unknown'),
                                     (STATE.replace('government', 'school_district:example/government'), 'local')]:
            with self.subTest(level=level):
                role = {'type': 'upper', 'jurisdiction': jurisdiction, 'title': 'Source title'}
                term = convert(person([role]))['terms'][0]
                self.assertEqual(term['jurisdiction_level'], level)
                self.assertEqual(term['role_type'], 'council' if level in ('city', 'county', 'local') else 'upper')
                self.assertEqual(term['title'], 'Source title')
        self.assertEqual(converter.jurisdiction_place_name(COUNTY, {}), 'Example')
        self.assertEqual(converter.jurisdiction_place_name(CITY, {CITY: 'Catalog name'}), 'Catalog name')

    def test_selection_method_requires_explicit_source_evidence(self):
        for method, expected in [('ELECTED', 'elected'), ('appointed', 'appointed'),
                                  ('acting', 'unknown'), (None, 'unknown')]:
            term = convert(person([{'type': 'governor', 'selection_method': method}]))['terms'][0]
            self.assertEqual(term['selection_method'], expected)
            self.assertEqual(term['selection_method_raw'], method)

    def test_parent_government_precedence_does_not_prove_containment_or_election(self):
        cases = [
            (COUNTY.replace('/government', '/district:1/government'), 'county'),
            (CITY.replace('/government', '/ward:1/government'), 'city'),
            (COUNTY.replace('/government', '/place:example/ward:1/government'), 'city'),
            (STATE.replace('/government', '/ward:1/government'), 'unknown'),
        ]
        for jurisdiction, expected in cases:
            with self.subTest(jurisdiction=jurisdiction):
                term = convert(person([{'type': 'upper', 'jurisdiction': jurisdiction}]))['terms'][0]
                self.assertEqual(term['jurisdiction_level'], expected)
                self.assertEqual(term['jurisdiction'], jurisdiction)
                self.assertEqual(term['role_type'], 'upper' if expected == 'unknown' else 'council')
                self.assertEqual(term['selection_method'], 'unknown')

    def test_contacts_ids_provenance_and_input_are_preserved(self):
        data = person([{'type': 'mayor', 'jurisdiction': COUNTY}])
        data.update(email='office@example.invalid', phone='synthetic-phone',
                    contact_form='https://example.invalid/form',
                    offices=[{'address': 'Synthetic office', 'voice': 'office-phone'}],
                    contact_details=[{'note': 'Legacy', 'email': 'legacy@example.invalid'}],
                    links=[{'url': 'https://example.invalid/site', 'note': 'official'}],
                    other_identifiers=[{'scheme': 'wikidata', 'identifier': 'synthetic-id'}],
                    sources=[{'url': 'https://example.invalid/source'}],
                    extras={'verified_at': '2030-06-01'})
        before = copy.deepcopy(data)
        result = convert(data)
        term = result['terms'][0]
        self.assertEqual(data, before)
        self.assertEqual(result['id'], {'openstates': 'synthetic-person-1', 'wikidata': 'synthetic-id'})
        self.assertEqual(term['contact_form'], data['email'])
        self.assertEqual(term['web_form'], data['contact_form'])
        self.assertEqual(term['phone'], data['phone'])
        self.assertEqual(term['addresses'], [
            {'address': 'Synthetic office', 'voice': 'office-phone'},
            {'note': 'Legacy', 'email': 'legacy@example.invalid'},
        ])
        self.assertEqual(term['email'], 'office@example.invalid')
        self.assertEqual(term['url'], 'https://example.invalid/site')
        self.assertEqual(term['sources'], ['https://example.invalid/source'])
        self.assertEqual(term['links'], data['links'])
        self.assertEqual(term['verified_at'], '2030-06-01')

    def test_legacy_contact_fallback_and_address_retention(self):
        office = {'note': 'Legacy office', 'address': 'Synthetic location',
                  'voice': 'synthetic-phone', 'fax': 'synthetic-fax', 'email': 'office@example.invalid'}
        result = convert(dict(person([]), contact_details=[office], roles=[{'type': 'mayor'}]))
        self.assertEqual(result['terms'][0]['contact_form'], office['email'])
        self.assertEqual(result['terms'][0]['phone'], office['voice'])
        parsed = converter.parse_office_address(office, parser=lambda address: [])
        self.assertEqual(parsed['raw_address'], office['address'])
        self.assertEqual(parsed['title'], office['note'])
        self.assertEqual(parsed['fax'], office['fax'])
        self.assertEqual(parsed['email'], office['email'])


class TermTests(unittest.TestCase):
    def test_generated_future_flag_is_not_source_evidence_after_start(self):
        for source_flag, expected in [(None, 'current'), (True, 'current'), (False, 'historical')]:
            with self.subTest(source_current=source_flag):
                role = {'type': 'mayor', 'start_date': '2031-01-01'}
                if source_flag is not None:
                    role['current'] = source_flag
                generated = convert(person([role]))['terms'][0]
                self.assertEqual((generated['term_status'], generated['current']), ('future', False))
                self.assertEqual(generated.get('source_current'), source_flag)
                after = converter.order_terms([generated], date(2031, 1, 1))[0]
                self.assertEqual(after['term_status'], expected)
                self.assertEqual(after['current'], expected == 'current')
                self.assertEqual((generated['term_status'], generated['current']), ('future', False))

    def test_future_equal_start_prefers_missing_end_before_display_marker(self):
        terms = [
            {'type': 'known-end', 'start': '2031-01-01', 'end': '2032-01-01', 'is_display_term': True},
            {'type': 'missing-end', 'start': '2031-01-01'},
        ]
        self.assertEqual(converter.order_terms(terms, AS_OF), [
            {'type': 'known-end', 'start': '2031-01-01', 'end': '2032-01-01',
             'term_status': 'future', 'current': False, 'is_display_term': False},
            {'type': 'missing-end', 'start': '2031-01-01',
             'term_status': 'future', 'current': False, 'is_display_term': True},
        ])

    def test_utc_day_rollover_requires_reclassification_without_source_change(self):
        term = {'type': 'mayor', 'start': '2029-01-01', 'end': '2030-06-15'}
        before = copy.deepcopy(term)
        self.assertEqual(converter.order_terms([term], AS_OF)[0]['term_status'], 'current')
        self.assertEqual(converter.order_terms([term], date(2030, 6, 16))[0]['term_status'], 'historical')
        self.assertEqual(term, before)

    def test_date_and_source_marker_classification(self):
        cases = [({}, 'ambiguous'), ({'end': '2031-01-01'}, 'ambiguous'),
                 ({'start': '2029-01-01'}, 'current'),
                 ({'start': '2031-01-01'}, 'future'),
                 ({'end': '2029-01-01'}, 'historical'),
                 ({'start': '2030-06-15', 'end': '2030-06-15'}, 'current'),
                 ({'start': 'bad'}, 'ambiguous'),
                 ({'end': '2030-02-30'}, 'ambiguous'),
                 ({'start': '2031-01-01', 'end': '2029-01-01'}, 'ambiguous'),
                 ({'source_current': True}, 'current'),
                 ({'source_current': False}, 'historical'),
                 ({'source_current': True, 'end': '2029-01-01'}, 'historical'),
                 ({'source_current': True, 'start': '2031-01-01'}, 'future')]
        for term, expected in cases:
            with self.subTest(term=term):
                self.assertEqual(converter.term_status(term, AS_OF), expected)

    def test_current_term_last_despite_undated_history_and_future(self):
        roles = [{'type': 'mayor', 'current': True},
                 {'type': 'lower', 'start_date': '2020-01-01', 'end_date': '2025-01-01'},
                 {'type': 'governor', 'start_date': '2031-01-01'},
                 {'type': 'unknown-office'}]
        result = convert(person(roles))
        self.assertEqual([
            (t['type'], t['term_status'], t['current'], t['is_display_term'],
             t.get('start'), t.get('end')) for t in result['terms']
        ], [
            ('unknown-office', 'ambiguous', None, False, None, None),
            ('lower', 'historical', False, False, '2020-01-01', '2025-01-01'),
            ('governor', 'future', False, False, '2031-01-01', None),
            ('mayor', 'current', True, True, None, None),
        ])
        self.assertEqual(result['term_status_as_of'], '2030-06-15')

    def test_display_fallback_does_not_claim_current(self):
        terms = [{'type': 'past', 'end': '2029-01-01'},
                 {'type': 'future', 'start': '2031-01-01'}, {'type': 'unknown'}]
        result = converter.order_terms(terms, AS_OF)
        self.assertEqual(result[-1]['type'], 'unknown')
        self.assertIsNone(result[-1]['current'])
        self.assertEqual(converter.order_terms(terms[:2], AS_OF)[-1]['type'], 'past')
        future = converter.order_terms(terms[1:2], AS_OF)[-1]
        self.assertEqual((future['term_status'], future['current']), ('future', False))

    def test_ties_stable_history_latest_and_future_earliest(self):
        terms = [{'type': 'first', 'start': '2020-01-01'},
                 {'type': 'second', 'start': '2020-01-01'}]
        self.assertEqual(converter.order_terms(terms, AS_OF), [
            {'type': 'second', 'start': '2020-01-01', 'term_status': 'current',
             'current': True, 'is_display_term': False},
            {'type': 'first', 'start': '2020-01-01', 'term_status': 'current',
             'current': True, 'is_display_term': True},
        ])
        past = [{'end': '2020-01-01'}, {'end': '2029-01-01'}]
        self.assertEqual(converter.order_terms(past, AS_OF), [
            {'end': '2020-01-01', 'term_status': 'historical',
             'current': False, 'is_display_term': False},
            {'end': '2029-01-01', 'term_status': 'historical',
             'current': False, 'is_display_term': True},
        ])
        future = [{'start': '2035-01-01'}, {'start': '2031-01-01'}]
        self.assertEqual(converter.order_terms(future, AS_OF), [
            {'start': '2035-01-01', 'term_status': 'future',
             'current': False, 'is_display_term': False},
            {'start': '2031-01-01', 'term_status': 'future',
             'current': False, 'is_display_term': True},
        ])
        self.assertEqual(converter.order_terms([], AS_OF), [])


class GenerationTests(unittest.TestCase):
    def test_both_extensions_sorted_repeatable_skip_and_force(self):
        # JSON is a YAML subset. Injecting its loader avoids native/libpostal
        # and repository utils side effects; only the orchestration is tested.
        with tempfile.TemporaryDirectory() as root:
            root = str(Path(root).resolve())
            source, output = Path(root) / 'data', Path(root) / 'output'
            category = source / 'xx' / 'municipalities'
            category.mkdir(parents=True)
            for filename, identity in [('b.yaml', 'second'), ('a.yml', 'first')]:
                data = dict(person([{'type': 'lower', 'jurisdiction': CITY}]), id=identity)
                (category / filename).write_text(json.dumps(data), encoding='utf-8')
            (source / 'xx' / 'municipalities.yaml').write_text(
                json.dumps([{'id': CITY, 'name': 'Catalog Example'}]), encoding='utf-8')

            def loader(path):
                return json.loads(Path(path).read_text(encoding='utf-8'))

            kwargs = dict(data_dir=str(source), output_dir=str(output), as_of=AS_OF,
                          loader=loader, state_names={'XX': 'Example State'}, address_parser=dict)
            converter.generate_legislator_json(**kwargs)
            destination = output / 'xx' / 'municipalities.json'
            first = destination.read_bytes()
            records = json.loads(first)
            self.assertEqual([p['id']['openstates'] for p in records], ['first', 'second'])
            self.assertEqual(records[0]['terms'][0]['place'], 'Catalog Example')
            converter.generate_legislator_json(**kwargs, force_refresh=True)
            self.assertEqual(destination.read_bytes(), first)
            (category / 'a.yml').unlink()
            converter.generate_legislator_json(**kwargs)
            self.assertEqual(destination.read_bytes(), first)
            converter.generate_legislator_json(**kwargs, force_refresh=True)
            self.assertEqual(len(json.loads(destination.read_bytes())), 1)

    def test_catalog_conflict_is_not_silently_swallowed(self):
        with tempfile.TemporaryDirectory() as root:
            for extension, name in [('yml', 'One'), ('yaml', 'Two')]:
                Path(root, 'municipalities.' + extension).write_text(
                    json.dumps([{'id': CITY, 'name': name}]), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                converter.load_place_names(root, lambda p: json.loads(Path(p).read_text()))


class CLITests(unittest.TestCase):
    """Exercise real CLI/YAML loading in copied, entirely synthetic checkouts."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.checkout = self.root / 'checkout'
        scripts = self.checkout / 'scripts'
        scripts.mkdir(parents=True)
        for name in ('alternate_bulk_formats.py', 'state_names.py', 'converter_safety.py'):
            shutil.copyfile(Path(__file__).parent / name, scripts / name)
        self.script = scripts / 'alternate_bulk_formats.py'
        self.source = self.checkout / 'data'
        self.category = self.source / 'ca' / 'municipalities'
        self.category.mkdir(parents=True)
        self.input = self.category / 'official.yml'
        self.input.write_text(
            'id: synthetic-person-1\nname: Example Official\n'
            'roles:\n'
            '  - type: lower\n    start_date: 2020-01-01\n    end_date: 2025-01-01\n'
            '  - type: mayor\n    jurisdiction: ' + CITY + '\n'
            '  - type: governor\n    start_date: 2031-01-01\n', encoding='utf-8')
        (self.category / 'second.yaml').write_text(
            'id: synthetic-person-2\nname: Second Official\nroles: []\n', encoding='utf-8')
        (self.source / 'ca' / 'municipalities.yaml').write_text(
            '- id: ' + CITY + '\n  name: Synthetic Catalog City\n', encoding='utf-8')
        # A stale/unreadable cache must not be deserialized or replaced.
        self.input.with_suffix('.yml.pickle').write_bytes(b'not a pickle')
        self.output = self.root / 'fresh' / 'json'
        self.cwd = self.root / 'unrelated-cwd'
        self.cwd.mkdir()

    def run_cli(self, *args):
        env = os.environ.copy()
        env.pop('PYTHONDONTWRITEBYTECODE', None)
        env.pop('PYTHONPATH', None)
        return subprocess.run([sys.executable, str(self.script), *map(str, args)],
                              cwd=self.cwd, env=env, text=True, capture_output=True, timeout=30)

    def snapshot(self):
        return {str(p.relative_to(self.checkout)): None if p.is_dir() else
                (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.checkout.rglob('*')}

    def test_isolated_cli_preserves_history_and_force_refreshes(self):
        args = ('--input-dir', self.source, '--output-dir', self.output,
                '--as-of', AS_OF.isoformat())
        before = self.snapshot()
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        destination = self.output / 'ca' / 'municipalities.json'
        first = destination.read_bytes()
        records = json.loads(first)
        self.assertEqual([p['id']['openstates'] for p in records],
                         ['synthetic-person-1', 'synthetic-person-2'])
        self.assertEqual(records[0]['term_status_as_of'], AS_OF.isoformat())
        terms = records[0]['terms']
        self.assertEqual([(t['type'], t['term_status']) for t in terms],
                         [('lower', 'historical'), ('governor', 'future'), ('mayor', 'ambiguous')])
        self.assertIsNone(terms[-1]['current'])
        self.assertNotIn('start', terms[-1])
        self.assertNotIn('end', terms[-1])
        self.assertEqual(terms[-1]['selection_method'], 'unknown')
        self.assertEqual(terms[-1]['place'], 'Synthetic Catalog City')
        self.assertEqual(terms[-1]['state'], 'California')
        result = self.run_cli(*args, '--force-refresh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(destination.read_bytes(), first)
        self.assertEqual(self.snapshot(), before)

        # Changed input with its original mtime proves force is not a timestamp heuristic.
        timestamp = self.input.stat().st_mtime_ns
        self.input.write_text(self.input.read_text().replace('Example Official', 'Changed Official'),
                              encoding='utf-8')
        os.utime(self.input, ns=(timestamp, timestamp))
        changed = self.snapshot()
        output_time = destination.stat().st_mtime_ns
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination.read_bytes(), destination.stat().st_mtime_ns),
                         (first, output_time))
        result = self.run_cli(*args, '--force-refresh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(destination.read_bytes())[0]['name']['official_full'],
                         'Changed Official')
        self.assertEqual(self.snapshot(), changed)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_legacy_default_layout_works_from_unrelated_directory(self):
        result = self.run_cli('--as-of', AS_OF.isoformat())
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.checkout / 'alternate_formats/json/ca/municipalities.json'
        self.assertEqual(len(json.loads(destination.read_bytes())), 2)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_invalid_cli_arguments_do_not_write(self):
        before = self.snapshot()
        for extra in [('--as-of', '2030-02-30'), ('--as-of', '20300615'),
                      ('--remove-pickles',), ('--unknown-option',)]:
            with self.subTest(extra=extra):
                result = self.run_cli('--output-dir', self.output, *extra)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.snapshot(), before)
        result = self.run_cli('--output-dir', self.source / 'generated')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_yaml_python_tags_are_rejected_without_output_or_source_changes(self):
        self.input.write_text('!!python/tuple [1, 2]\n', encoding='utf-8')
        before = self.snapshot()
        result = self.run_cli('--output-dir', self.output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('could not determine a constructor', result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_keys_merges_and_recursive_aliases_fail_closed(self):
        cases = [
            ('id: one\nid: two\nroles: []\n', 'duplicate YAML mapping key'),
            ('id: one\nroles: []\nroles: []\n', 'duplicate YAML mapping key'),
            ('id: one\nroles: [{type: mayor, type: governor}]\n', 'duplicate YAML mapping key'),
            ('id: one\nextras: {true: a, 1: b}\n', 'duplicate YAML mapping key'),
            ('id: one\nextras: {<<: {a: 1}, a: 2}\n', 'merge directives'),
            ('id: one\nextras: {<<: {a: 1}, <<: {b: 2}}\n', 'merge directives'),
            ('id: one\nextras: &loop {again: *loop}\n', 'recursive YAML aliases'),
        ]
        for text, message in cases:
            with self.subTest(text=text):
                self.input.write_text(text, encoding='utf-8')
                before = self.snapshot()
                result = self.run_cli('--output-dir', self.output, '--force-refresh')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.snapshot(), before)

    def test_benign_aliases_preserve_all_roles(self):
        self.input.write_text('id: alias-person\nname: Alias Official\nroles:\n'
                              '  - &role {type: mayor}\n  - *role\n', encoding='utf-8')
        before = self.snapshot()
        result = self.run_cli('--output-dir', self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads((self.output / 'ca/municipalities.json').read_bytes())
        self.assertEqual([term['type'] for term in records[0]['terms']], ['mayor', 'mayor'])
        self.assertEqual(self.snapshot(), before)

    def test_symlink_destinations_fail_before_any_write_even_when_skipping(self):
        for kind in ('state', 'file', 'dangling', 'root', 'ancestor'):
            for force in (False, True):
                with self.subTest(kind=kind, force=force):
                    base = self.root / (kind + str(force))
                    base.mkdir()
                    output = base / 'output'
                    target = base / 'target'
                    target.mkdir()
                    sentinel = target / 'sentinel'
                    sentinel.write_bytes(b'untouched')
                    target_before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns,
                                     target.stat().st_mtime_ns)
                    missing = target / 'missing'
                    if kind == 'root':
                        output.symlink_to(target, target_is_directory=True)
                    elif kind == 'ancestor':
                        link = base / 'link'
                        link.symlink_to(target, target_is_directory=True)
                        output = link / 'new-output'
                    else:
                        output.mkdir()
                        if kind == 'state':
                            (output / 'ca').symlink_to(self.source / 'ca', target_is_directory=True)
                        else:
                            (output / 'ca').mkdir()
                            (output / 'ca/municipalities.json').symlink_to(
                                missing if kind == 'dangling' else self.input)
                    before = self.snapshot()
                    result = self.run_cli('--output-dir', output,
                                          *(['--force-refresh'] if force else []))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Symlink output path', result.stderr)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual((sentinel.read_bytes(), sentinel.stat().st_mtime_ns,
                                      target.stat().st_mtime_ns), target_before)
                    self.assertEqual(list(target.iterdir()), [sentinel])
                    self.assertFalse(missing.exists())

    def test_force_replaces_hardlinked_output_without_truncating_source(self):
        destination = self.output / 'ca/municipalities.json'
        destination.parent.mkdir(parents=True)
        os.link(self.input, destination)
        before = self.snapshot()
        result = self.run_cli('--output-dir', self.output, '--force-refresh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(destination.read_bytes())), 2)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
