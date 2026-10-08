# Open States People

![Lint YAML](https://github.com/openstates/people/workflows/Lint%20YAML/badge.svg)

This repository contains YAML files with official information on state legislators, governors, and some municipal leaders.

## Links

* [People Issues](https://github.com/openstates/issues/issues?q=is%3Aissue+is%3Aopen+label%3Adata%3Apeople)
* [Contributor's Guide](https://docs.openstates.org/contributing/)
* [Documentation](https://docs.openstates.org/contributing/people/)
* [Open States Discussions](https://github.com/openstates/issues/discussions)
* [Code of Conduct](https://docs.openstates.org/code-of-conduct/)


## Data Layout

All data within the data directory is organized by state.  Within a given state directory you may find the following:

  * legislature - people that are currently serving in the legislature
  * executive - people that are currently serving in the state executive (e.g. governors)
  * municipalities - people currently serving in local government (e.g. mayors)
  * retired - people not currently serving any tracked roles
  * committees - committee data

## Civiq converter development

The generated contract is documented in [schema.md](schema.md#civiq-generated-personterm-contract).
`scripts/alternate_bulk_formats.py` preserves legacy fields and adds explicit
role, jurisdiction, selection-method, and temporal metadata. Unknown does not
mean elected; a selected display term does not necessarily mean current.

Run the offline synthetic regression suite from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_alternate_bulk_formats.py' -v
```

Pure tests inject loading/address parsing; CLI tests copy the converter and its
state-name/safety modules into temporary synthetic checkouts and use real safe YAML
loading in subprocesses. They verify destination isolation, skip/force behavior,
history/uncertainty, and rejected unsafe YAML tags. CLI fixtures have no offices:
these tests do not validate the native libpostal installation or regenerate
production data. The converter API accepts `data_dir`, `output_dir`, an explicit
`as_of` date, and injected loading/parsing functions for staged builds and tests.
Without `force_refresh=True` (CLI: `--force-refresh`), existing output files are
left untouched. A revision change alone does not force regeneration.

### Direct JSON build CLI

Use Python 3.9+ with PyYAML installed. Converting source office addresses also
requires the existing `postal.parser` binding and native libpostal installation.
No dependency installation is performed by the CLI or tests.

```sh
python3 /path/to/people-civiq/scripts/alternate_bulk_formats.py \
  --input-dir /path/to/people-civiq/data \
  --output-dir /path/to/fresh-people-json \
  --force-refresh --as-of 2030-06-15
```

`--output-dir` is the JSON root itself: outputs are `<root>/<state>/<category>.json`,
not `<root>/json/...`. It must be outside the input data tree. Relative arguments
are relative to the caller's working directory; omitted paths are relative to
the script's checkout (`data` and `alternate_formats/json`). `--as-of` accepts
only `YYYY-MM-DD`, defaulting to the current UTC date. Use a consistent reference
date for reproducible builds. Generation reads YAML with a strict `SafeLoader` subclass, ignores
existing pickle caches and never writes them. Direct CLI execution also suppresses
Python bytecode caches. Only explicit legacy `--remove-pickles` maintenance deletes
source caches; it cannot be combined with generation options.

Strict YAML policy: duplicate mapping keys at every depth fail, including keys
that compare equal after safe construction (such as `true` and `1`). All YAML
merge directives (`<<`) are rejected before flattening, even disjoint merges;
spell out mappings instead of relying on implicit override precedence. Ordinary
nonrecursive aliases remain supported; recursive aliases fail explicitly.

Output paths must have no symlink components, including root ancestors, state
directories, existing files and dangling links. Existing output trees are checked
before generation, even when files would normally be skipped. Supply physical
paths rather than symlink aliases (for example, platform temporary-dir aliases).
On POSIX, writes traverse directories using no-follow descriptors and replace
files from fresh sibling inodes, so force refresh cannot truncate a hard-linked
source. Normal skip/force semantics remain unchanged. Per-file replacement is
atomic, but earlier files can remain if a later conversion fails: a nonzero exit
must never authorize publication. Use a caller-owned staging directory without
concurrent writers. Source bytes and modification times are untouched; reading
may update access times according to filesystem policy. Source snapshot/digest
binding and independent consistency validation belong to the build coordinator.

The converter does not publish atomically, remove stale destination files, or
prove coverage. Build into a fresh external directory, force conversion, and
validate before publication. No source categories or missing dates are invented.

Backend staged publication, runtime date re-evaluation, and coordinated Flutter
decoding are separate completion steps. Do not pin an uncommitted converter
revision or treat this change as proof of nationwide data completeness. Never
hand-edit source person records or generate production output as part of tests.

## About this Repo

A lot of inspiration was taken from the [congress-legislators](https://github.com/unitedstates/congress-legislators) project that has been maintaining this data for the United States Congress.

New as of 2021: the data/us directory is also directly ported from the congress-legislators repo, reproduced here in our schema for ease of use for people using both data sets.

Historically Open States has scraped this data, but given the relatively infrequent changes and the manual labor required to retire & merge legislators- we have decided to move in this direction in the hopes of improving the data and making it more accessible for contributors.

Also, please note that this portion of the project is in the public domain in the United States with all copyright waived via a [CC0](https://creativecommons.org/publicdomain/zero/1.0/) dedication.  By contributing you agree to waive all copyright claims.
