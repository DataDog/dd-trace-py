import _io
import os
import re


try:
    from ddtrace import __version__ as version
except ImportError:
    from ddtrace import get_version

    version = get_version()

# Some old versions could not have or export some symbols, so we import them dynamically and assign None if not found
# which will make the aspect benchmark fail but not the entire benchmark
symbols = [
    "add_aspect",
    "add_inplace_aspect",
    "bytearray_aspect",
    "bytearray_extend_aspect",
    "bytes_aspect",
    "bytesio_aspect",
    "capitalize_aspect",
    "casefold_aspect",
    "decode_aspect",
    "encode_aspect",
    "format_aspect",
    "format_map_aspect",
    "index_aspect",
    "join_aspect",
    "ljust_aspect",
    "lower_aspect",
    "modulo_aspect",
    "replace_aspect",
    "repr_aspect",
    "slice_aspect",
    "str_aspect",
    "stringio_aspect",
    "swapcase_aspect",
    "title_aspect",
    "translate_aspect",
    "upper_aspect",
    "rstrip_aspect",
    "lstrip_aspect",
    "strip_aspect",
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


def iast_add_aspect(tainted_str="test"):
    return add_aspect(tainted_str, " world")  # noqa: F821


def add_noaspect(tainted_str="test"):
    return tainted_str + " world"


def iast_add_inplace_aspect(tainted_str="test"):
    return add_inplace_aspect(tainted_str, "second_string")  # noqa: F821


def add_inplace_noaspect(tainted_str="test"):
    tainted_str = "second_string"
    return tainted_str


def iast_bytearray_aspect(tainted_ba=b"test"):
    return bytearray_aspect(bytearray, 0, tainted_ba)  # noqa: F821


def bytearray_noaspect(tainted_ba=b"test"):
    return bytearray(tainted_ba=b"test")


def iast_bytearray_extend_aspect(tainted_ba=b"test"):
    ba = bytearray(tainted_ba)
    bytearray_extend_aspect(None, 0, ba, b" world")  # noqa: F821
    return ba

def bytearray_extend_noaspect(tainted_ba=b"test"):
    ba = bytearray(tainted_ba)
    ba.extend(b" world")
    return ba


def iast_bytes_aspect(tainted_str="test"):
    return bytes_aspect(bytes, 0, tainted_str, "utf-8")  # noqa: F821


def bytes_noaspect(tainted_str="test"):
    return bytes(tainted_str, "utf-8")


def iast_bytesio_aspect(tainted_b=b"data"):
    x = bytesio_aspect(None, 0, tainted_b)  # noqa: F821
    return x.read()


def bytesio_noaspect(tainted_b=b"data"):
    x = _io.BytesIO(tainted_b)
    return x.read()


def iast_capitalize_aspect(tainted_str="example"):
    return capitalize_aspect(str, 0, tainted_str)  # noqa: F821


def capitalize_noaspect(tainted_str="example"):
    return tainted_str.capitalize()


def iast_casefold_aspect(tainted_str="EXAMPLE"):
    return casefold_aspect(str, 0, tainted_str)  # noqa: F821


def casefold_noaspect(tainted_str="EXAMPLE"):
    return tainted_str.casefold()


def iast_decode_aspect(tainted_b=b"data"):
    return decode_aspect(str, 0, tainted_b, "utf-8")  # noqa: F821


def decode_noaspect(tainted_b=b"data"):
    return tainted_b.decode("utf-8")


def iast_encode_aspect(tainted_str="hello"):
    return encode_aspect(bytes, 0, tainted_str, "utf-8")  # noqa: F821


def encode_noaspect(tainted_str="hello"):
    return tainted_str.encode("utf-8")


def iast_format_aspect(tainted_str="Hello, {}!"):
    return format_aspect(None, 1, tainted_str, "World")  # noqa: F821


def format_noaspect(tainted_str="Hello, {}!"):
    return tainted_str.format("World")


def iast_format_map_aspect(*args, **kwargs):
    return format_map_aspect(None, 1, "{greeting}, World!", {"greeting": "Hello"})  # noqa: F821


def format_map_noaspect(*args, **kwargs):
    return "{greeting}, World!".format_map({"greeting": "Hello"})


def iast_index_aspect(tainted_str="example"):
    return index_aspect(tainted_str, 3)  # noqa: F821


def index_noaspect(tainted_str="example"):
    return tainted_str[3]


def iast_join_aspect(*args, **kwargs):
    return join_aspect(None, 1, ", ", ["one", "two", "three"])  # noqa: F821


def join_noaspect(*args, **kwargs):
    return ", ".join(["one", "two", "three"])


def iast_lower_aspect(tainted_str="EXAMPLE"):
    return lower_aspect(None, 1, tainted_str)  # noqa: F821


def lower_noaspect(tainted_str="EXAMPLE"):
    return tainted_str.lower()


def iast_ljust_aspect(tainted_str="example"):
    return ljust_aspect(None, 1, tainted_str, 10)  # noqa: F821


def ljust_noaspect(tainted_str="example"):
    return tainted_str.ljust(10)


def iast_modulo_aspect(*args, **kwargs):
    return modulo_aspect("hello %s", "foo")  # noqa: F821


def iast_modulo_aspect_for_bytes(*args, **kwargs):
    return modulo_aspect(b"hello %s", b"foo")  # noqa: F821


def iast_modulo_aspect_for_bytes_bytearray(*args, **kwargs):
    return modulo_aspect(b"hello %s", bytearray(b"foo"))  # noqa: F821


def iast_modulo_aspect_for_bytearray_bytearray(*args, **kwargs):
    return modulo_aspect(bytearray(b"hello %s"), bytearray(b"foo"))  # noqa: F821


def modulo_noaspect(*args, **kwargs):
    return "{} {}".format("hello", "world")


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


def iast_re_sub_aspect(*args, **kwargs):
    return re_sub_aspect(None, 1, re.compile("/"), "_", "foo/bar")  # noqa: F821


def re_sub_noaspect(*args, **kwargs):
    return re.sub("/", "_", "foo/bar")


def iast_rsplit_aspect(*args, **kwargs):
    return rsplit_aspect(None, 0, "foo bar baz")  # noqa: F821


def rsplit_noaspect(*args, **kwargs):
    return "foo bar baz".rsplit(*args, **kwargs)


def iast_splitlines_aspect(*args, **kwargs):
    return splitlines_aspect(None, 0, "line1\nline2\nline3")  # noqa: F821


def splitlines_noaspect(*args, **kwargs):
    return "line1\nline2\nline3".splitlines(*args, **kwargs)


def iast_str_aspect(*args, **kwargs):
    return str_aspect(str, 0, 42)  # noqa: F821


def str_noaspect(*args, **kwargs):
    return str(42)


def iast_stringio_aspect(*args, **kwargs):
    io = stringio_aspect(None, 0, "data")  # noqa: F821
    return io.read(*args, **kwargs)


def stringio_noaspect(*args, **kwargs):
    io = _io.StringIO("data")
    return io.read(*args, **kwargs)


def iast_repr_aspect(*args, **kwargs):
    return repr_aspect(None, 0, 42)  # noqa: F821


def repr_noaspect(*args, **kwargs):
    return repr(42)


def iast_slice_aspect(tainted_str="example"):
    return slice_aspect(  # noqa: F821
        tainted_str,
        1,
        3,
        1,
    )


def slice_noaspect(tainted_str="example"):
    return tainted_str[1:3:1]


def iast_replace_aspect(tainted_str="example"):
    return replace_aspect(None, 1, tainted_str, "example", "foo")  # noqa: F821


def replace_noaspect(tainted_str="example"):
    return tainted_str.replace("example", "foo")


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
    return re.match(r"(\w+) (\w+)", "Hello World").groups(*args, **kwargs)


def iast_re_group_aspect(*args, **kwargs):
    return re_group_aspect(None, 0, re.match(r"(\w+) (\w+)", "Hello World"))  # noqa: F821


def re_group_noaspect(*args, **kwargs):
    return re.match(r"(\w+) (\w+)", "Hello World").group(*args, **kwargs)


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


def iast_upper_aspect(tainted_str="example"):
    return upper_aspect(None, 1, tainted_str)  # noqa: F821


def upper_noaspect(tainted_str="example"):
    return tainted_str.upper()


def iast_translate_aspect(tainted_str="example"):
    return translate_aspect(None, 1, tainted_str, {101: 105})  # noqa: F821


def translate_noaspect(tainted_str="example"):
    return tainted_str.translate({101: 105})


def iast_title_aspect(tainted_str="hello world"):
    return title_aspect(None, 1, tainted_str)  # noqa: F821


def title_noaspect(tainted_str="hello world"):
    return tainted_str.title()


def iast_swapcase_aspect(tainted_str="Hello World"):
    return swapcase_aspect(None, 1, tainted_str)  # noqa: F821


def swapcase_noaspect(tainted_str="Hello World"):
    return tainted_str.swapcase()


def iast_split_aspect(tainted_str="foo bar baz"):
    return split_aspect(None, 1, tainted_str)  # noqa: F821


def split_noaspect(tainted_str="foo bar baz"):
    return tainted_str.split()


def iast_strip_aspect(tainted_str="    foo bar baz    "):
    return strip_aspect(None, 1, tainted_str)  # noqa: F821


def strip_noaspect(tainted_str="    foo bar baz    "):
    return tainted_str.strip()


def iast_rstrip_aspect(tainted_str="    foo bar baz    "):
    return rstrip_aspect(None, 1, tainted_str)  # noqa: F821


def rstrip_noaspect(tainted_str="    foo bar baz    "):
    return tainted_str.rstrip()


def iast_lstrip_aspect(tainted_str="    foo bar baz    "):
    return lstrip_aspect(None, 1, tainted_str)  # noqa: F821


def lstrip_noaspect(tainted_str="    foo bar baz    "):
    return tainted_str.lstrip()
