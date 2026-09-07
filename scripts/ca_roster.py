#!/usr/bin/env python3
"""Parse the California Roster (Secretary of State, "Incorporated Cities and
Town Officials") into the JSON format consumed by municipal_from_sources.py.

Input is the roster's text, one page per "=== PAGE n ===" section, extracted
from a two-column PDF layout (left column top-to-bottom, then right column
top-to-bottom, per page). Each entry looks like:

  City of Albany
  ( County of Alameda )
  Address: ...
  Mayor: <name>
  Vice Mayor/Mayor Pro Tempore: <name>
  City Council: <name>
  <name>
  <name>
  1st and 3rd Tuesdays at 7 pm
  City Manager: ...
  ...
  Incorporated: 4/18/1854
  Legislative Districts: AD: 18 CD: 12 SD: 7
  Chartered City Population: 78280

A city header is detected by the line right after it starting "( County of"
(two entries in the 2026 roster print the header without "City of"/"Town of",
so this is more reliable than matching the prefix). The council roster is
free text across cities: one name per line, comma-separated lists wrapped
across lines, "Councilmember"/"District N"/"D1 - " role decorations, etc. --
extract_names() below handles the patterns observed in the 2026 edition.

Usage:
  python3 scripts/ca_roster.py --text data/sources/municipal/ca/ca-roster-2026.txt \
      --exclude data/sources/municipal/ca/san-diego-county.json \
      -o data/sources/municipal/ca/ca-roster-2026.json
"""
import argparse
import json
import re
import sys

SOURCE_URL = "https://admin.cdn.sos.ca.gov/ca-roster/2026/cities-towns.pdf"
SOURCE_NAME = "California Roster 2026, Secretary of State"
DEFAULT_VERIFIED_AT = "2026-01-01"

COUNCIL_LABELS = ("City Council", "Council")
# The 2026 edition prints "Vice Mayor/Mayor Pro Tempore:" almost everywhere,
# but a handful of alternate labels for the same seat are handled too (most
# specific first, so the compound label isn't mistaken for a shorter one).
VICE_MAYOR_LABELS = (
    "Vice Mayor/Mayor Pro Tempore", "Mayor Pro Tempore", "Mayor Pro Tem", "Vice Mayor",
)
# Other field labels, used to know where the council block ends. A handful
# of entries print a space before the colon ("City Attorney :"), so labels
# are matched with a regex rather than a literal ":" suffix.
OTHER_LABEL_WORDS = [
    "Address", "Telephone", "Fax", "Website", "Email", "Office Hours",
    "Mayor",
    "City Manager", "City Clerk", "City Attorney", "Treasurer",
    "Police Chief", "Fire Chief", "School Superintendent",
    "Incorporated", "Legislative Districts", "Chartered City Population",
] + list(VICE_MAYOR_LABELS)


def label_re(word):
    return re.compile(r'^' + re.escape(word) + r'\s*:\s*')


OTHER_LABEL_RES = [label_re(w) for w in OTHER_LABEL_WORDS]
COUNCIL_LABEL_RES = [label_re(w) for w in COUNCIL_LABELS]

SCHEDULE_RE = re.compile(
    r'\b(mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?|'
    r'meets?|meeting|recess|quarterly|\d{1,2}(:\d{2})?\s*(a\.?m\.?|p\.?m\.?))\b',
    re.I,
)
SCHEDULE_FIRST_WORD_RE = re.compile(
    r'^(every|first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|tri-weekly)\b', re.I
)
# A trailing "<ordinal> and <ordinal>" (e.g. "At Large 1st and 3rd") is a
# meeting-schedule fragment glued onto a name by a mid-phrase line wrap.
TRAILING_ORDINAL_RE = re.compile(
    r'\s+\d{1,2}(st|nd|rd|th)\s+and\s+\d{1,2}(st|nd|rd|th)\b.*$', re.I
)

ROLE_WORDS_RE = re.compile(
    r'^(mayor pro tempore|mayor pro tem|vice mayor|councilmember|'
    r'council member|councilwoman|councilman|mayor)$',
    re.I,
)
ROLE_PREFIX_RE = re.compile(
    r'^(mayor pro tempore|mayor pro tem|vice mayor|councilmember|'
    r'council member|councilwoman|councilman|mayor)'
    r'(\s+of\s+district\s+\d+\w*)?\s*-?\s*',
    re.I,
)
LEADING_AND_RE = re.compile(r'^and\s+', re.I)
NON_PERSON_RE = re.compile(r'\b(vacant|none|n/?a|changes|rotat\w*)\b', re.I)
DIST_PREFIX_RE = re.compile(r'^D\d+\s*-\s*', re.I)
DESCRIPTOR_RE = re.compile(
    r'^(district\s+\d+.*|council district\s+\d+.*|councilmember|'
    r'council member|councilwoman|councilman|mayor pro tem\w*|'
    r'vice mayor.*|mayor|president|vice president|council president|'
    r'at large|there (is|are)\b.*|\d{1,2}(st|nd|rd|th))$',
    re.I,
)
FOR_YEAR_PREFIX_RE = re.compile(r'^for\s+\d{4}\s*:?\s*', re.I)
TRAILING_DISTRICT_RE = re.compile(r'[\s-]+(council )?district\s+\d+\w*\s*$', re.I)
# A redundant inner sub-header some cities print before the actual list,
# e.g. "City Council: Council Members: Bonnie Peat, ...".
INNER_COUNCIL_LABEL_RE = re.compile(r'^(city )?council members\s*:?\s*', re.I)
SUFFIX_RE = re.compile(r'^(jr\.?|sr\.?|ii|iii|iv)$', re.I)
HONORIFIC_RE = re.compile(r',?\s*(ph\.?d\.?|m\.?d\.?|esq\.?|cpa|mpa|j\.?d\.?)\.?\s*$', re.I)
PAREN_RE = re.compile(r'\s*\([^)]*\)')


def clean_name(name):
    """Strip parentheticals/credential honorifics/a glued district suffix,
    collapse whitespace."""
    name = PAREN_RE.sub('', name)
    name = HONORIFIC_RE.sub('', name)
    name = TRAILING_DISTRICT_RE.sub('', name)
    name = re.sub(r'\s+', ' ', name).strip().strip(',').strip()
    return name


PAGE_MARKER_RE = re.compile(r'^===.*===\s*$')


def split_entries(text):
    """Return [(header_line, [block_lines])] for each city entry. Page
    markers ("=== PAGE n ===") are dropped first, so an entry that happens
    to span a page break in the source reads as one continuous block."""
    lines = [l for l in text.split('\n') if not PAGE_MARKER_RE.match(l.strip())]
    starts = []
    for i in range(len(lines) - 1):
        cur = lines[i].strip()
        nxt = lines[i + 1].strip()
        if cur and re.match(r'^\(\s*County of', nxt):
            starts.append(i)
    entries = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        entries.append((lines[start].strip(), lines[start:end]))
    return entries


def city_name(header):
    m = re.match(r'^(?:City|Town) of (.+)$', header)
    return m.group(1).strip() if m else header.strip()


def find_county(block_lines):
    for l in block_lines[:3]:
        m = re.search(r'County of\s+(.+?)\s*\)', l)
        if m:
            county = m.group(1).strip()
            county = re.sub(r'\s+County$', '', county, flags=re.I)
            return county
    return None


def find_vice_mayor(block_lines):
    """Return (label, raw_value) for whichever vice-mayor/mayor-pro-tem
    label is present, or (None, None)."""
    for word in VICE_MAYOR_LABELS:
        val = field_value(block_lines, word)
        if val is not None:
            return word, val
    return None, None


def field_value(block_lines, word):
    """Value after `word:` (space before the colon tolerated), pulling in
    the next line too when the value looks cut off mid-name (a bare word,
    or a dangling role label). Returns None if the label is absent."""
    lab_re = label_re(word)
    for i, l in enumerate(block_lines):
        s = l.strip()
        m = lab_re.match(s)
        if not m:
            continue
        val = s[m.end():].strip()
        if val and _open_ended(val) and i + 1 < len(block_lines):
            nxt = block_lines[i + 1].strip()
            if (nxt and not re.match(r'^\(\s*County of', nxt)
                    and not any(r.match(nxt) for r in OTHER_LABEL_RES)
                    and not any(r.match(nxt) for r in COUNCIL_LABEL_RES)):
                val = val + ' ' + nxt
        return val
    return None


def council_block_lines(block_lines):
    """Lines belonging to the council roster, with trailing meeting-schedule
    line(s) removed."""
    start = None
    first_val = None
    for i, l in enumerate(block_lines):
        s = l.strip()
        for lab_re in COUNCIL_LABEL_RES:
            m = lab_re.match(s)
            if m:
                start = i
                first_val = INNER_COUNCIL_LABEL_RE.sub('', s[m.end():].strip(), count=1)
                break
        if start is not None:
            break
    if start is None:
        return []
    lines = [first_val] if first_val else []
    for l in block_lines[start + 1:]:
        s = l.strip()
        if not s:
            continue
        if any(lab_re.match(s) for lab_re in OTHER_LABEL_RES):
            break
        if re.match(r'^\(\s*County of', s):
            break
        lines.append(s)
    # Everything from the first meeting-schedule line to the end of the
    # block is schedule/location detail, not more names (schedules can run
    # several lines and mix in an address, so trim from the first match
    # rather than only popping matching lines off the tail).
    for i, l in enumerate(lines):
        if SCHEDULE_RE.search(l) or SCHEDULE_FIRST_WORD_RE.match(l):
            return lines[:i]
    return lines


def _open_ended(buf):
    """True if buf looks mid-list: it ends with a comma/"and", or its last
    comma-separated segment is a single bare word (a first name wrapped
    without its surname, e.g. "...Adrin Nazarian, Bob")."""
    b = buf.rstrip()
    if b.count('(') > b.count(')'):
        return True
    if b.endswith(',') or re.search(r'\band$', b, re.I):
        return True
    last_seg = b.rsplit(',', 1)[-1].strip()
    if len(last_seg.split()) <= 1:
        return True
    # A dangling role label ("...Karen Schwartz, Council Member") with no
    # name yet attached is also mid-item.
    if ROLE_WORDS_RE.match(last_seg):
        return True
    # A role label wrapped mid-phrase ("...Cathy Warner, and Council" /
    # "Member of District 4 - ...") leaves a bare fragment word at the end.
    last_word = re.sub(r'[^\w]', '', b.split()[-1]) if b.split() else ''
    return last_word.lower() in ('council', 'mayor', 'vice')


def merge_continuations(lines):
    """Join a wrapped name/list line onto the previous one when the previous
    line looks incomplete (see _open_ended)."""
    items = []
    buf = None
    for l in lines:
        if buf is None:
            buf = l
            continue
        if _open_ended(buf):
            buf = buf + ' ' + l
        else:
            items.append(buf)
            buf = l
    if buf is not None:
        items.append(buf)
    return items


def extract_names(lines):
    """Turn council block lines into a flat list of person names, dropping
    role/district decorations and re-joining "X and Y" and name suffixes."""
    names = []
    for item in merge_continuations(lines):
        item = DIST_PREFIX_RE.sub('', item.strip())
        for part in item.split(','):
            part = TRAILING_ORDINAL_RE.sub('', part).strip()
            part = LEADING_AND_RE.sub('', part).strip()
            if not part:
                continue
            part = ROLE_PREFIX_RE.sub('', part).strip()
            part = LEADING_AND_RE.sub('', part).strip()
            if DESCRIPTOR_RE.match(part):
                continue
            if SUFFIX_RE.match(part) and names:
                names[-1] = names[-1] + ', ' + part
                continue
            for sub in re.split(r'\s+and\s+', part):
                sub = clean_name(sub)
                if sub:
                    names.append(sub)
    return names


def parse_entry(header, block_lines):
    city = city_name(header)
    county = find_county(block_lines)

    mayor_raw = field_value(block_lines, "Mayor")
    if mayor_raw:
        mayor_raw = FOR_YEAR_PREFIX_RE.sub('', mayor_raw).strip()
        mayor_raw = ROLE_PREFIX_RE.sub('', mayor_raw).strip()
    mayor_name = clean_name(mayor_raw) if mayor_raw else None
    mayor = None
    if mayor_name and not NON_PERSON_RE.search(mayor_name):
        mayor = {"name": mayor_name}

    council = []
    seen = set()
    vm_label, vm_raw = find_vice_mayor(block_lines)
    if vm_raw:
        vm_raw = FOR_YEAR_PREFIX_RE.sub('', vm_raw).strip()
        vm_raw = ROLE_PREFIX_RE.sub('', vm_raw).strip()
        vm_name = clean_name(vm_raw)
        if vm_name and not NON_PERSON_RE.search(vm_name):
            council.append({"name": vm_name, "note": vm_label})
            seen.add(vm_name.lower())

    for name in extract_names(council_block_lines(block_lines)):
        key = name.lower()
        if key in seen:
            continue
        if mayor_name and key == mayor_name.lower():
            continue
        seen.add(key)
        council.append({"name": name})

    out = {
        "city": city,
        "county": county,
        "source_url": SOURCE_URL,
        "council": council,
    }
    if mayor:
        out["mayor"] = mayor
    return out


def parse_roster_text(text):
    return [parse_entry(header, block) for header, block in split_entries(text)]


def parse_pdf(path):
    """Thin fallback using pdfplumber: same left/right column split as the
    text path, one page at a time. Not exercised in this environment."""
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            words = page.extract_words()
            width = page.width
            left = [w for w in words if w['x0'] < width / 2]
            right = [w for w in words if w['x0'] >= width / 2]

            def col_lines(ws):
                rows = {}
                for w in ws:
                    y = round(w['top'])
                    key = next((k for k in rows if abs(k - y) <= 2), y)
                    rows.setdefault(key, []).append(w)
                out = []
                for y in sorted(rows):
                    row = sorted(rows[y], key=lambda w: w['x0'])
                    out.append(' '.join(w['text'] for w in row))
                return out

            pages.append('=== PAGE %d ===\n%s' % (i, '\n'.join(col_lines(left) + col_lines(right))))
    return '\n\n'.join(pages)


def load_excluded_cities(path):
    if not path:
        return set()
    with open(path) as f:
        doc = json.load(f)
    return {c["city"] for c in doc.get("cities", [])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="Path to extracted roster text")
    ap.add_argument("--pdf", help="Path to the roster PDF (requires pdfplumber)")
    ap.add_argument("--exclude", help="JSON file whose cities[].city entries are skipped")
    ap.add_argument("--verified-at", default=DEFAULT_VERIFIED_AT)
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args(argv)

    if not args.text and not args.pdf:
        ap.error("one of --text or --pdf is required")

    if args.text:
        with open(args.text, encoding="utf-8") as f:
            text = f.read()
    else:
        text = parse_pdf(args.pdf)
    cities = parse_roster_text(text)

    excluded = load_excluded_cities(args.exclude)
    if excluded:
        cities = [c for c in cities if c["city"] not in excluded]

    doc = {
        "verified_at": args.verified_at,
        "source": SOURCE_NAME,
        "cities": cities,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("Parsed %d cities -> %s" % (len(cities), args.output), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
