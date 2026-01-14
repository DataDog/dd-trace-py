import re


try:
    from ddtrace import __version__ as version
except ImportError:
    from ddtrace import get_version

    version = get_version()

# Some old versions could not have or export some symbols, so we import them dynamically and assign None if not found
# which will make the aspect benchmark fail but not the entire benchmark
symbols = [
    "re_expand_aspect",
    "re_findall_aspect",
    "re_finditer_aspect",
    "re_fullmatch_aspect",
    "re_group_aspect",
    "re_groups_aspect",
    "re_match_aspect",
    "re_search_aspect",
    "re_sub_aspect",
    "re_subn_aspect",
]

notfound_symbols = []

for symbol in symbols:
    try:
        # Dynamically import the symbol from the module and assign to globals
        globals()[symbol] = __import__("ddtrace.appsec._iast._taint_tracking.aspects", fromlist=[symbol]).__dict__[
            symbol
        ]
    except (ImportError, KeyError):
        # If the symbol is not found, assign None and print a warning
        globals()[symbol] = None
        notfound_symbols.append(symbol)
        # print(f"Warning: {symbol} not found in the current version")

if notfound_symbols:
    print("Warning: symbols not found in the tested version [%s]: %s" % (version.version, str(notfound_symbols)))


def iast_re_sub_aspect(*args, **kwargs):
    return re_sub_aspect(None, 1, re.compile("/"), "_", "foo/bar")  # noqa: F821


def re_sub_noaspect(*args, **kwargs):
    return re.sub("/", "_", "foo/bar")


def iast_re_subn_aspect(*args, **kwargs):
    return re_subn_aspect(None, 1, re.compile("/"), "_", "foo/bar")  # noqa: F821


def re_subn_noaspect(*args, **kwargs):
    return re.subn("/", "_", "foo/bar")


def iast_re_search_aspect(*args, **kwargs):
    return re_search_aspect(None, 1, re.compile("foo"), "foo bar")  # noqa: F821


def re_search_noaspect(*args, **kwargs):
    return re.search("foo", "foo bar")


def iast_re_match_aspect(*args, **kwargs):
    return re_match_aspect(None, 1, re.compile("foo"), "foo bar")  # noqa: F821


def re_match_noaspect(*args, **kwargs):
    return re.match("foo", "foo bar")


def iast_re_groups_aspect(*args, **kwargs):
    return re_groups_aspect(None, 0, re.match(r"(\w+) (\w+)", "Hello World"))  # noqa: F821


def re_groups_noaspect(*args, **kwargs):
    return re.match(r"(\w+) (\w+)", "Hello World").groups()


def iast_re_group_aspect(*args, **kwargs):
    return re_group_aspect(None, 0, re.match(r"(\w+) (\w+)", "Hello World"))  # noqa: F821


def re_group_noaspect(*args, **kwargs):
    return re.match(r"(\w+) (\w+)", "Hello World").group()


def iast_re_fullmatch_aspect(*args, **kwargs):
    return re_fullmatch_aspect(None, 1, re.compile("foo"), "foo")  # noqa: F821


def re_fullmatch_noaspect(*args, **kwargs):
    return re.fullmatch("foo", "foo")


def iast_re_finditer_aspect(*args, **kwargs):
    return re_finditer_aspect(None, 1, re.compile("foo"), "foo bar foo")  # noqa: F821


def re_finditer_noaspect(*args, **kwargs):
    return re.finditer("foo", "foo bar foo")


def iast_re_findall_aspect(*args, **kwargs):
    return re_findall_aspect(None, 1, re.compile("foo"), "foo bar foo")  # noqa: F821


def re_findall_noaspect(*args, **kwargs):
    return re.findall("foo", "foo bar foo")


def iast_re_expand_aspect(*args, **kwargs):
    re_obj = re.compile(r"(\w+) (\w+)")
    match = re.match(re_obj, "Hello World")
    return re_expand_aspect(None, 1, match, "Salute: \\1 Subject: \\2")  # noqa: F821


def re_expand_noaspect(*args, **kwargs):
    re_obj = re.compile(r"(\w+) (\w+)")
    match = re.match(re_obj, "Hello World")
    return match.expand("Salute: \\1 Subject: \\2")
