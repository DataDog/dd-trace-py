import ddtrace._version as version

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
    "lower_aspect",
    "ljust_aspect",
    "modulo_aspect",
    "ospathbasename_aspect",
    "ospathdirname_aspect",
    "ospathjoin_aspect",
    "ospathnormcase_aspect",
    "ospathsplit_aspect",
    "ospathsplitdrive_aspect",
    "ospathsplitext_aspect",
    "re_sub_aspect",
    "rsplit_aspect",
    "splitlines_aspect",
    "str_aspect",
    "stringio_aspect",
    "repr_aspect",
    "slice_aspect",
    "replace_aspect",
    "re_subn_aspect",
    "re_search_aspect",
    "re_match_aspect",
    "re_groups_aspect",
    "re_group_aspect",
    "re_fullmatch_aspect",
    "re_finditer_aspect",
    "re_findall_aspect",
    "re_expand_aspect",
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

print("Warning: symbols not found in the tested version [%s]: %s", (version.version, str(notfound_symbols)))
import _io
import os
import re


def iast_add_aspect():
    return add_aspect(3, 4)


def add_noaspect():
    return 3 + 4


def iast_add_inplace_aspect():
    return add_inplace_aspect(42, 1)


def add_inplace_noaspect():
    a = 42
    a += 1
    return a


def iast_bytearray_aspect():
    return bytearray_aspect(bytearray, 0, b"test")


def bytearray_noaspect():
    return bytearray(b"test")


def iast_bytearray_extend_aspect():
    ba = bytearray(b"hello")
    bytearray_extend_aspect(None, 0, ba, b" world")


def bytearray_extend_noaspect():
    ba = bytearray(b"hello")
    ba.extend(b" world")


def iast_bytes_aspect():
    return bytes_aspect(bytes, 0, "hello", "utf-8")


def bytes_noaspect():
    return bytes("hello", "utf-8")


def iast_bytesio_aspect():
    x = bytesio_aspect(None, 0, b"data")
    return x.read()


def bytesio_noaspect():
    x = _io.BytesIO(b"data")
    return x.read()


def iast_capitalize_aspect():
    return capitalize_aspect(str, 0, "example")


def capitalize_noaspect():
    return "example".capitalize()


def iast_casefold_aspect():
    return casefold_aspect(str, 0, "EXAMPLE")


def casefold_noaspect():
    return "EXAMPLE".casefold()


def iast_decode_aspect():
    return decode_aspect(str, 0, b"hello", "utf-8")


def decode_noaspect():
    return b"hello".decode("utf-8")


def iast_encode_aspect():
    return encode_aspect(bytes, 0, "hello", "utf-8")


def encode_noaspect():
    return "hello".encode("utf-8")


def iast_format_aspect():
    return format_aspect(None, 1, "Hello, {}!", "World")


def format_noaspect():
    return "Hello, {}!".format("World")


def iast_format_map_aspect():
    return format_map_aspect(None, 1, "{greeting}, World!", {"greeting": "Hello"})


def format_map_noaspect():
    return "{greeting}, World!".format_map({"greeting": "Hello"})


def iast_index_aspect():
    return index_aspect("example", 3)


def index_noaspect():
    return "example"[3]


def iast_join_aspect():
    return join_aspect(None, 1, ", ", ["one", "two", "three"])


def join_noaspect():
    return ", ".join(["one", "two", "three"])


def iast_lower_aspect():
    return lower_aspect(None, 1, "EXAMPLE")


def lower_noaspect():
    return "EXAMPLE".lower()


def iast_ljust_aspect():
    return ljust_aspect(None, 1, "example", 10)


def ljust_noaspect():
    return "example".ljust(10)


def iast_modulo_aspect():
    return modulo_aspect("hello %s", "foo")


def modulo_noaspect():
    return "{} {}".format("hello", "world")


def iast_ospathbasename_aspect():
    return ospathbasename_aspect("/path/to/file")


def ospathbasename_noaspect():
    return os.path.basename("/path/to/file")


def iast_ospathdirname_aspect():
    return ospathdirname_aspect("/path/to/file")


def ospathdirname_noaspect():
    return os.path.dirname("/path/to/file")


def iast_ospathjoin_aspect():
    return ospathjoin_aspect("/path", "to", "file")


def ospathjoin_noaspect():
    return os.path.join("/path", "to", "file")


def iast_ospathnormcase_aspect():
    return ospathnormcase_aspect("example")


def ospathnormcase_noaspect():
    return os.path.normcase("example")


def iast_ospathsplit_aspect():
    return ospathsplit_aspect("/path/to/file")


def ospathsplit_noaspect():
    return os.path.split("/path/to/file")


def iast_ospathsplitdrive_aspect():
    return ospathsplitdrive_aspect("/path/to/file")


def ospathsplitdrive_noaspect():
    return os.path.splitdrive("/path/to/file")


def iast_ospathsplitext_aspect():
    return ospathsplitext_aspect("/path/to/file")


def ospathsplitext_noaspect():
    return os.path.splitext("/path/to/file")


def iast_re_sub_aspect():
    return re_sub_aspect(None, 1, re.compile("/"), "_", "foo/bar")


def re_sub_noaspect():
    return re.sub("/", "_", "foo/bar")


def iast_rsplit_aspect():
    return rsplit_aspect(None, 0, "foo bar baz")


def rsplit_noaspect():
    return "foo bar baz".rsplit()


def iast_splitlines_aspect():
    return splitlines_aspect(None, 0, "line1\nline2\nline3")


def splitlines_noaspect():
    return "line1\nline2\nline3".splitlines()


def iast_str_aspect():
    return str_aspect(str, 0, 42)


def str_noaspect():
    return str(42)


def iast_stringio_aspect():
    io = stringio_aspect(None, 0, "data")
    return io.read()


def stringio_noaspect():
    io = _io.StringIO("data")
    return io.read()


def iast_repr_aspect():
    return repr_aspect(None, 0, 42)


def repr_noaspect():
    return repr(42)


def iast_slice_aspect():
    return slice_aspect(
        "example",
        1,
        3,
        1,
    )


def slice_noaspect():
    return "example"[1:3:1]


def iast_replace_aspect():
    return replace_aspect(None, 1, "example", "example", "foo")


def replace_noaspect():
    return "example".replace("example", "foo")


def iast_re_subn_aspect():
    return re_subn_aspect(None, 1, re.compile("/"), "_", "foo/bar")


def re_subn_noaspect():
    return re.subn("/", "_", "foo/bar")


def iast_re_search_aspect():
    return re_search_aspect(None, 1, re.compile("foo"), "foo bar")


def re_search_noaspect():
    return re.search("foo", "foo bar")


def iast_re_match_aspect():
    return re_match_aspect(None, 1, re.compile("foo"), "foo bar")


def re_match_noaspect():
    return re.match("foo", "foo bar")


def iast_re_groups_aspect():
    return re_groups_aspect(None, 0, re.match(r"(\w+) (\w+)", "Hello World"))


def re_groups_noaspect():
    return re.match(r"(\w+) (\w+)", "Hello World").groups()


def iast_re_group_aspect():
    return re_group_aspect(None, 0, re.match(r"(\w+) (\w+)", "Hello World"))


def re_group_noaspect():
    return re.match(r"(\w+) (\w+)", "Hello World").group()


def iast_re_fullmatch_aspect():
    return re_fullmatch_aspect(None, 1, re.compile("foo"), "foo")


def re_fullmatch_noaspect():
    return re.fullmatch("foo", "foo")


def iast_re_finditer_aspect():
    return re_finditer_aspect(None, 1, re.compile("foo"), "foo bar foo")


def re_finditer_noaspect():
    return re.finditer("foo", "foo bar foo")


def iast_re_findall_aspect():
    return re_findall_aspect(None, 1, re.compile("foo"), "foo bar foo")


def re_findall_noaspect():
    return re.findall("foo", "foo bar foo")


def iast_re_expand_aspect():
    re_obj = re.compile(r"(\w+) (\w+)")
    match = re.match(re_obj, "Hello World")
    return re_expand_aspect(None, 1, match, "Salute: \\1 Subject: \\2")


def re_expand_noaspect():
    re_obj = re.compile(r"(\w+) (\w+)")
    match = re.match(re_obj, "Hello World")
    return match.expand("Salute: \\1 Subject: \\2")
