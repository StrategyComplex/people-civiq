# Person Schema

* id: UUID representing this person for this data set.  **required**
* name: Full Name.  **required**
* given_name: First name.
* family_name: Last name.
* middle_name: Middle name or initial.
* suffix: Name Suffix.
* gender: Male/Female/Other
* email: Email address.
* biography: Official biography text.
* birth_date: Birth date in YYYY-MM-DD format.
* death_date: Death date in YYYY-MM-DD format.
* image: URL to official photo.
* ids:  nested dictionary of additional ids
    * twitter: username of official Twitter account
    * youtube: username of official YouTube account
    * instagram: username of official Instagram account
    * facebook: username of official Facebook account
* party: list of parties that the legislator has been a part of, each may have the following fields:
    * name: Name of the party.    **required**
    * start_date
    * end_date
* roles: list of legislative & executive roles held by this individual, each may have the following fields:
    * type: upper|lower|legislature|governor|lt_governor|mayor    **required**
    * district: name/number of district   **required if upper|lower|legislature**
    * jurisdiction: ocd-jurisdiction identifier **required**
    * start_date  **required if not upper|lower|legislature**
    * end_date    **required if not upper|lower|legislature**
    * end_reason: reason this role ended, such as resignation/death
* extras - unvalidated JSON to store additional details in
* contact_details (see below)
* links (see below)
* other_identifiers (see below)
* other_names (see below)
* sources (see below)

## Civiq generated person/term contract

`scripts/alternate_bulk_formats.py` emits the existing array of persons with
`id`, `name`, `bio`, and `terms`. This section describes generated JSON, not a
change to the source person schema above. IDs and all source roles are retained.
No role title establishes whether its holder was elected or appointed.

Additive person field: `term_status_as_of` is a UTC calendar date (`YYYY-MM-DD`).
Temporal metadata is a snapshot at that date, not a permanent assertion; the
backend must re-evaluate it when serving later dates. No person `status` is added.

Each term retains legacy `type`, `state`, optional `start`, `end`, `district`,
`jurisdiction`, `place`, `party`, contacts, and provenance. New fields:

| Field | Contract |
| --- | --- |
| `raw_type` | Original role `type`, unchanged; legacy `type` is also unchanged. |
| `role_type` | Known aliases normalize to `lieutenant_governor`, `attorney_general`, `secretary_of_state`, `chief_election_officer`, `governor`, `mayor`, or `council`. City/county/local `upper`/`lower` become `council` here only. Unknown types remain verbatim. |
| `title` | Source role title when supplied; no invented title. |
| `jurisdiction_level` | `city`, `county`, `local`, `state`, `national`, or `unknown`, derived from OCD segments, never a mailing address. |
| `selection_method` | `elected`, `appointed`, or `unknown`; only explicit role metadata establishes the first two. |
| `selection_method_raw` | Original role value, if supplied, including unsupported values. |
| `source_current` | Original role `current` marker, if supplied; only boolean values have selection semantics. |
| `term_status` | `current`, `historical`, `future`, or `ambiguous`. |
| `current` | `true` for current, `false` for historical/future, JSON `null` for ambiguous. |
| `is_display_term` | Exactly one `true` for a nonempty term array; that term is last. Not proof of incumbency. |

### Temporal classification and legacy display selection

At the reference UTC day, apply these rules in order:

1. Invalid nonempty dates or end before start: ambiguous.
2. Start after today: future. End before today: historical. End today remains
   eligible. Dates override a stale source current marker.
3. Explicit `source_current: false`: historical.
4. Explicit `source_current: true`, or a known start on/before today: current.
5. Otherwise ambiguous (including no start and a future end without a marker).

Display selection prefers current, then ambiguous, historical, future. Within
current/ambiguous, latest parsed start then latest parsed end wins; missing dates
sort oldest. Historical prefers latest end then start. Future prefers earliest
start then end. Equal candidates choose the first source role. All remaining
terms are stably sorted by parsed start then end (missing/invalid dates oldest),
and the selected term is moved last. Concurrent roles are never discarded.
An ambiguous/historical/future-only person still has a display term, but must
not be presented as a verified incumbent. No estimated dates are generated.

Derived `current: false` on a future term must not become source evidence when
the start day arrives. Reclassify using `source_current`, not the derived flag.
For equal future starts, a missing end sorts before a known end (date-minimum
semantics), even if the known-end term previously carried a display marker.

### Jurisdictions and contacts

Place segments take precedence over county segments, then recognized local
segments (`district:`, `school_district:`, `township:`). Unknown subdivisions
remain unknown rather than becoming statewide seats. Catalog names take
precedence; place/county identifiers provide display-name fallbacks.

Parent precedence is intentional even with deeper subdivisions: county plus
district remains `county`; place plus ward remains `city`; place outranks county
when both occur. The complete identifier must survive. This government-level
classification proves neither GPS constituency containment nor elected selection.
Clients must still reject unresolved wards/districts for In District filtering.

Both `offices` and legacy `contact_details` are retained in `addresses`, with
exact duplicate input objects removed across the two shapes. Source address,
email, fax, and office title are preserved alongside parsed components.
Top-level email/phone precede the first office email/voice. `email` is additive;
`contact_form` retains the legacy email-or-form behavior. An explicit source form
is additionally retained in `web_form`; `links` retains all source links while
legacy `url` uses the last nonempty URL. Contacts are person-level source data
copied to terms, not evidence that a historical office had those contacts.

Generation includes `.yml` and `.yaml`, sorts paths, and rejects conflicting
catalog names. Repeated builds at the same reference date and inputs produce
identical bytes. Publication is not atomic; generate into staging, validate, then
publish a complete dataset in the backend. Native address/YAML dependencies
remain unchanged; pure normalization tests inject these edges.

# Committee Schema

* id: UUID representing this organization.  **required**
* jurisdiction: ocd-jurisdiction identifier **required**
* name: Name of Committee.  **required**
* chamber: Chamber of this committee, can be:
    * upper
    * lower
    * legislature
    **required**
* classification: Classification, can be:
    * committee
    * subcommittee
    **required**
* parent: `id` of parent committee, if classification is subcommittee.
* members: list of memberships, each may have the following:
    * id - ocd-person ID if known
    * name - name of person **required**
    * role - role that person fills on committee, if not 'member'
    * start_date - optional start date of this membership
    * end_date - optional end date of this membership
* links (see below)
* sources (see below)
* other_names (see below)

### Common Elements

These sections can have a list of objects, each with the following fields available.

* contact_details:
    * note: "District Office" or "Capitol Office"  **required**
    * address: Mailing address.
    * voice: Phone number used for voice calls.
    * fax: Fax number.

* links:
    * note: description of the purpose of this link
    * url: URL associated with legislator **required**

* other_identifiers:
    * scheme: origin of this identifier (e.g. "votesmart")        **required**
    * identifier: identifier used by the given service/scheme (e.g. 13823)    **required**
    * start_date: optional date identifier started being valid for this person
    * end_date: optional date identifier ceased to be valid for this person

* other_names:
    * name: alternate name that has been seen for this person **required**
        * if a new name has strange spacing, you must quote the entire entry
        * e.g. `name: "Stephanie  T. Bolan"` vs. `name: Stephanie  T. Bolan`
        * If you neglect to do this, yaml _will_ remove additional spaces
    * start_date: optional date name started being valid for this person
    * end_date: optional date name ceased to be valid for this person

* sources:
    * note: description of the usage of this source
    * url: URL used to collect information for this person **required**
