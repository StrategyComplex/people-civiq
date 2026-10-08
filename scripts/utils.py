# Helpful functions for finding data about members and committees
# Copied from Congress-Legislators Project
import contextlib
import email.utils
import errno
import hashlib
import html.entities
import json
import os
import pickle
import pprint
import re
import smtplib
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path

import lxml.html  # for meta redirect parsing

# Legacy runtime dependency is absent from the lint environment.
import rtyaml  # ty: ignore[unresolved-import]
import scrapelib
import yaml
from pytz import timezone
from state_names import states as states  # noqa: PLC0414 -- legacy public re-export

CURRENT_CONGRESS = 115
_JANUARY_TRANSITION_DAY = 3
_TRANSITION_HOUR = 12
_MARCH_FOURTH_END_CONGRESS = 69
_SHORTENED_CONGRESS = 73
_JANUARY_TRANSITION_YEAR = 1935


# read in an opt-in config file for supplying email settings
# returns None if it's not there, and this should always be handled gracefully
path = "email/config.yml"
if Path(path).exists():
    with Path(path).open() as config:
        email_settings = yaml.safe_load(config).get("email", None)
else:
    email_settings = None


def congress_from_legislative_year(year):
    return ((year + 1) / 2) - 894


def legislative_year(date=None):
    if not date:
        date = datetime.now()

    if date.month == 1:
        if date.day in (1, 2):
            return date.year - 1
        if (
            date.day == _JANUARY_TRANSITION_DAY
            and isinstance(date, datetime)
            and date.hour < _TRANSITION_HOUR
        ):
            return date.year - 1
    return date.year


def congress_start_end_dates(congress):
    # Get the start and end dates of a Congress (e.g. 1 for the 1st Congress).
    # The end date of one Congress is identical to the start of the next
    # because the switchover is at noon (at least since 1935).
    # Also see get_congress_from_date.
    start_year = 1789 + (congress - 1) * 2
    end_year = start_year + 2
    if congress < _SHORTENED_CONGRESS:
        # The 1st Congress met on March 4, 1789, per an act of the Continental
        # Congress adopted Sept 13, 1788. The Constitutional term period would
        # end two years later. Actual adjournments suggest that Congress
        # believed its term ended on March 3rd.
        if congress != _MARCH_FOURTH_END_CONGRESS:
            return (date(start_year, 3, 4), date(end_year, 3, 3))
        # Only the 69th Congress adjourned on March 4, perhaps viewing its term
        # as expiring at the actual time of day the first Congress began.
        # Preserve March 4 as the end date for that Congress, as in our data.
        return (date(start_year, 3, 4), date(end_year, 3, 4))
    if congress == _SHORTENED_CONGRESS:
        # The 20th Amendment shortened the 73rd Congress, from March 4 to
        # January 3, 1935 (at noon), rather than the usual March 3.
        # Congress adjourned in 1934 anyway.
        return (date(start_year, 3, 4), date(end_year, 1, 3))
    # Starting with the 74th Congress, Congresses begin and end on January
    # 3rds at noon.
    return (date(start_year, 1, 3), date(end_year, 1, 3))


def get_congress_from_date(d, range_type=None):
    # This is the inverse of congress_start_end_dates.
    #
    # Return the Congress number that the date 'd' occurs in by first computing
    # the 'legislative year' it occurs in, and then using some simple arithmetic
    # counting back to 1789 (the first legislative year) and dividing by two
    # (since Congresses are two years).
    #
    # Transition dates are ambiguous because Congresses change at noon (since
    # 1935, but treated similarly before then). 'start' assigns the date to the
    # next Congress; 'end' assigns it to the previous Congress.
    if (d.year % 2) == 0:
        # Even years occur entirely within a Congress.
        y = d.year
    else:
        # In odd years, dates before the transition (and the transition date
        # itself for range_type='end') belong to the previous legislative year.
        # Through 1933 the transition was March 4, despite most adjournments
        # occurring by March 3. Since 1935 it is January 3.
        td = (
            date(d.year, 3, 4)
            if d.year < _JANUARY_TRANSITION_YEAR
            else date(d.year, 1, 3)
        )
        if d < td:
            y = d.year - 1
        elif d > td:
            y = d.year
        elif range_type == "end":
            # Assign this date to the previous Congress.
            y = d.year - 1
        elif range_type == "start":
            # Assign this date to the next Congress.
            y = d.year
        else:
            message = f"Date {d} is ambiguous; must pass range_type='start' or 'end'."
            raise ValueError(message)

    # Now do some simple integer math to compute the Congress number.
    return ((y + 1) // 2) - 894


def parse_date(date):
    return datetime.strptime(date, "%Y-%m-%d").date()


# Keep the public keyword argument for legacy callers.
def log(object):  # noqa: A002
    if isinstance(object, str):
        print(object)
    else:
        pprint.pprint(object)


def uniq(seq):
    seen = set()
    seen_add = seen.add
    return [x for x in seq if x not in seen and not seen_add(x)]


def args():
    return [token for token in sys.argv[1:] if not token.startswith("--")]


def flags():
    options = {}
    for token in sys.argv[1:]:
        if token.startswith("--"):
            if "=" in token:
                key, value = token.split("=")
            else:
                key, value = token, True

            key = key.split("--")[1]
            if value == "True":
                value = True
            elif value == "False":
                value = False
            options[key.lower()] = value
    return options


##### Data management


def data_dir():
    return ".."


def load_data(path):
    # Preserve spelling, trailing slashes and string paths for YAML cache keys.
    return yaml_load(os.path.join(data_dir(), path))  # noqa: PTH118


def save_data(data, path):
    # As in load_data, do not normalize legacy YAML paths.
    yaml_dump(data, os.path.join(data_dir(), path))  # noqa: PTH118
    write(
        json.dumps(data, default=format_datetime),
        f"../alternate_formats/{path.replace('.yaml', '.json')}",
    )


##### Downloading

scraper = scrapelib.Scraper(requests_per_minute=60, retry_attempts=3)
scraper.user_agent = (
    "the @unitedstates project (https://github.com/unitedstates/congress-legislators)"
)


def cache_dir():
    return "cache"


def _download_body(url, options):
    if options.get("urllib", False):
        response = urllib.request.urlopen(url)
        body = response.read()
        if not options.get("binary", False):
            body = body.decode("utf-8")  # guessing encoding
    else:
        response = scraper.get(url)
        body = response.text if not options.get("binary", False) else response.content
    return body


def _follow_meta_redirect(html_tree, body, url, options):
    meta = html_tree.xpath(
        "//meta[translate(@http-equiv, 'REFSH', 'refsh') = 'refresh']/@content"
    )
    if meta:
        attr = meta[0]
        _wait, text = attr.split(";")
        if text.lower().startswith("url="):
            new_url = text[4:]
            if not new_url.startswith(url):  # don't print if a local redirect
                print(f"Found redirect for {url}, downloading {new_url} instead..")

            options.pop("check_redirects")
            body = download(new_url, None, True, options)
    return body


def download(url, destination=None, force=False, options=None):
    if not destination and not force:
        message = "destination must not be None if force is False."
        raise TypeError(message)

    if not options:
        options = {}

    # get the path to cache the file, or None if destination is None
    # Preserve destination spelling and trailing slashes for I/O and logging.
    cache = (
        os.path.join(cache_dir(), destination) if destination else None  # noqa: PTH118
    )

    # Path would strip a trailing slash and change the filesystem lookup.
    if not force and cache is not None and os.path.exists(cache):  # noqa: PTH110
        if options.get("debug", False):
            log(f"Cached: ({cache}, {url})")

        with open(cache) as f:  # noqa: PTH123 -- retain trailing-slash semantics
            body = f.read()
    else:
        try:
            if options.get("debug", False):
                log(f"Downloading: {url}")

            body = _download_body(url, options)
        except scrapelib.HTTPError:
            log(f"Error downloading {url}")
            return None

        # don't allow 0-byte files
        if (not body) or (not body.strip()):
            return None

        # the downloader can optionally parse the body as HTML
        # and look for meta redirects. a bit expensive, so opt-in.
        if options.get("check_redirects", False):
            try:
                html_tree = lxml.html.fromstring(body)
            except ValueError:
                log(f"Error parsing source from url {url}")
                return None

            body = _follow_meta_redirect(html_tree, body, url, options)

        # cache content to disk
        if cache:
            write(body, cache)

    return body


eastern_time_zone = timezone("US/Eastern")


def format_datetime(obj):
    if isinstance(obj, datetime):
        return eastern_time_zone.localize(obj.replace(microsecond=0)).isoformat()
    if isinstance(obj, str):
        return obj
    return None


def write(content, destination):
    # Preserve the default text encoding and binary mode for non-string content.
    # dirname preserves empty parents and the parent of a trailing-slash path.
    mkdir_p(os.path.dirname(destination))  # noqa: PTH120
    # Path.open would strip trailing slashes, potentially writing a different file.
    mode = "w" if isinstance(content, str) else "wb"
    with open(destination, mode) as f:  # noqa: PTH123
        f.write(content)


# mkdir -p in python, from:
# http://stackoverflow.com/questions/600268/mkdir-p-functionality-in-python
def mkdir_p(path):
    try:
        # Keep empty-path errors and lexical components (including trailing /.).
        os.makedirs(path)  # noqa: PTH103
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise


# The public argument is retained; this helper formats the active exception.
def format_exception(exception):  # noqa: ARG001
    exc_type, exc_value, exc_traceback = sys.exc_info()
    return "\n".join(traceback.format_exception(exc_type, exc_value, exc_traceback))


# taken from http://effbot.org/zone/re-sub.htm#unescape-html
def unescape(text, encoding=None):
    def remove_unicode_control(value):
        remove_re = re.compile("[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]")
        return remove_re.sub("", value)

    def fixup(m):
        text = m.group(0)
        if text[:2] == "&#":
            # Character reference; invalid references remain unchanged.
            if encoding is None:
                try:
                    if text[:3] == "&#x":
                        return chr(int(text[3:-1], 16))
                    return chr(int(text[2:-1]))
                except ValueError:
                    pass
            else:
                try:
                    if text[:3] == "&#x":
                        return bytes([int(text[3:-1], 16)]).decode(encoding)
                    return bytes([int(text[2:-1])]).decode(encoding)
                except ValueError:
                    pass
        else:
            # Named entity; unknown names remain unchanged.
            with contextlib.suppress(KeyError):
                text = chr(html.entities.name2codepoint[text[1:-1]])
        return text  # leave as is

    text = re.sub(r"&#?\w+;", fixup, text)
    return remove_unicode_control(text)


##### YAML serialization ######

# Apply some common settings for loading/dumping YAML and cache the
# data in pickled format which is a LOT faster than YAML.


def yaml_load(path, use_cache=True):
    # Loading YAML is ridiculously slow, so cache the YAML data
    # in a pickled file which loads much faster.

    # Check if the .pickle file exists and a hash stored inside it
    # matches the hash of the YAML file, and if so unpickle it.
    # Keep the YAML path's trailing-slash semantics; cache suffixes are filenames.
    with open(path, "rb") as source:  # noqa: PTH123
        h = hashlib.sha1(source.read()).hexdigest()
    if use_cache and Path(path + ".pickle").exists():
        try:
            with Path(path + ".pickle").open("rb") as cache:
                store = pickle.load(cache)
            if store["hash"] == h:
                return store["data"]
        except EOFError:
            pass  # bad .pickle file, pretend it doesn't exist

    # No cached pickled data exists, so load the YAML file.
    with open(path) as source:  # noqa: PTH123 -- retain trailing-slash semantics
        data = rtyaml.load(source)

    # Store in a pickled file for fast access later.
    with Path(path + ".pickle").open("wb") as cache:
        pickle.dump({"hash": h, "data": data}, cache)

    return data


def yaml_dump(data, path):
    # write file
    # Path.open would strip trailing slashes, potentially writing a different file.
    with open(path, "w") as destination:  # noqa: PTH123
        rtyaml.dump(data, destination)

    # Store in a pickled file for fast access later.
    with open(path, "rb") as source:  # noqa: PTH123 -- retain trailing-slash semantics
        h = hashlib.sha1(source.read()).hexdigest()
    with Path(path + ".pickle").open("wb") as cache:
        pickle.dump({"hash": h, "data": data}, cache)


# if email settings are supplied, email the text - otherwise, just print it
def admin(body):
    try:
        if isinstance(body, Exception):
            body = format_exception(body)

        print(body)  # always print it

        if email_settings:
            send_email(body)

    # Keep reporting failures at this boundary from triggering a reporting loop.
    except Exception as exception:  # noqa: BLE001
        print("Exception logging message to admin, halting as to avoid loop")
        print(format_exception(exception))


# this should only be called if the settings are definitely there
def send_email(message):
    settings = email_settings
    if settings is None:
        return
    print(f"Sending email to {settings['to']}...")

    # adapted from http://www.doughellmann.com/PyMOTW/smtplib/
    msg = MIMEText(message)
    msg.set_unixfrom("author")
    msg["To"] = email.utils.formataddr(("Recipient", settings["to"]))
    msg["From"] = email.utils.formataddr((settings["from_name"], settings["from"]))
    msg["Subject"] = f"{settings['subject']} - {int(time.time()):d}"

    server = smtplib.SMTP(settings["hostname"])
    try:
        server.ehlo()
        if settings["starttls"] and server.has_extn("STARTTLS"):
            server.starttls()
            server.ehlo()

        server.login(settings["user_name"], settings["password"])
        server.sendmail(settings["from"], [settings["to"]], msg.as_string())
    finally:
        server.quit()

    print(f"Sent email to {settings['to']}.")


def remove_pickles(output_dir):
    # remove all .pickle files in the given output_dir and its subdirectories
    for root, _dirs, files in os.walk(output_dir):
        for file in files:
            if file.endswith(".pickle"):
                # Keep the caller's lexical root spelling in the removal log.
                pickle_path = os.path.join(root, file)  # noqa: PTH118
                print(f"Removing pickle file: {pickle_path}")
                Path(pickle_path).unlink()
