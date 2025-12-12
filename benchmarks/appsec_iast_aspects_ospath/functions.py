import os


try:
    from ddtrace import __version__ as version
except ImportError:
    from ddtrace import get_version

    version = get_version()

# Some old versions could not have or export some symbols, so we import them dynamically and assign None if not found
# which will make the aspect benchmark fail but not the entire benchmark
symbols = [
    "ospathbasename_aspect",
    "ospathdirname_aspect",
    "ospathjoin_aspect",
    "ospathnormcase_aspect",
    "ospathsplit_aspect",
    "ospathsplitdrive_aspect",
    "ospathsplitext_aspect",
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


def iast_ospathbasename_aspect(*args, **kwargs):
    return ospathbasename_aspect("/path/to/file")  # noqa: F821


def ospathbasename_noaspect(*args, **kwargs):
    return os.path.basename("/path/to/file")


def iast_ospathdirname_aspect(*args, **kwargs):
    return ospathdirname_aspect("/path/to/file")  # noqa: F821


def ospathdirname_noaspect(*args, **kwargs):
    return os.path.dirname("/path/to/file")


def iast_ospathjoin_aspect(*args, **kwargs):
    return ospathjoin_aspect("/path", "to", "file")  # noqa: F821


def ospathjoin_noaspect(*args, **kwargs):
    return os.path.join("/path", "to", "file")


def iast_ospathnormcase_aspect(*args, **kwargs):
    return ospathnormcase_aspect("example")  # noqa: F821


def ospathnormcase_noaspect(*args, **kwargs):
    return os.path.normcase("example")


def iast_ospathsplit_aspect(*args, **kwargs):
    return ospathsplit_aspect("/path/to/file")  # noqa: F821


def ospathsplit_noaspect(*args, **kwargs):
    return os.path.split("/path/to/file")


def iast_ospathsplitdrive_aspect(*args, **kwargs):
    return ospathsplitdrive_aspect("/path/to/file")  # noqa: F821


def ospathsplitdrive_noaspect(*args, **kwargs):
    return os.path.splitdrive("/path/to/file")


def iast_ospathsplitext_aspect(*args, **kwargs):
    return ospathsplitext_aspect("/path/to/file")  # noqa: F821


def ospathsplitext_noaspect(*args, **kwargs):
    return os.path.splitext("/path/to/file")
