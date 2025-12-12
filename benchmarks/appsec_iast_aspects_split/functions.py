try:
    from ddtrace import __version__ as version
except ImportError:
    from ddtrace.version import get_version

    version = get_version()


# Some old versions could not have or export some symbols, so we import them dynamically and assign None if not found
# which will make the aspect benchmark fail but not the entire benchmark
symbols = [
    "rsplit_aspect",
    "split_aspect",
    "split_aspect",
    "splitlines_aspect",
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
    print("Warning: symbols not found in the tested version [%s]: %s" % (version, str(notfound_symbols)))


def iast_rsplit_aspect(*args, **kwargs):
    return rsplit_aspect(None, 0, "foo bar baz")  # noqa: F821


def rsplit_noaspect(*args, **kwargs):
    return "foo bar baz".rsplit()


def iast_splitlines_aspect(*args, **kwargs):
    return splitlines_aspect(None, 0, "line1\nline2\nline3")  # noqa: F821


def splitlines_noaspect(*args, **kwargs):
    return "line1\nline2\nline3".splitlines()


def iast_split_aspect(*args, **kwargs):
    return split_aspect(None, 1, "foo bar baz")  # noqa: F821


def split_noaspect(*args, **kwargs):
    return "foo bar baz".split()
