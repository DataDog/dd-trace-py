import _io
import os
import re

import ddtrace._version as version


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
    print("Warning: symbols not found in the tested version [%s]: %s" % (version.version, str(notfound_symbols)))


def iast_add_aspect():
    return add_aspect(3, 4)  # noqa: F821


def add_noaspect():
    return 3 + 4


def iast_add_inplace_aspect():
    return add_inplace_aspect(42, 1)  # noqa: F821


def add_inplace_noaspect():
    a = 42
    a += 1
    return a


def iast_bytearray_aspect():
    return bytearray_aspect(bytearray, 0, b"test")  # noqa: F821


def bytearray_noaspect():
    return bytearray(b"test")


def iast_bytearray_extend_aspect():
    ba = bytearray(b"hello")
    bytearray_extend_aspect(None, 0, ba, b" world")  # noqa: F821


def bytearray_extend_noaspect():
    ba = bytearray(b"hello")
    ba.extend(b" world")


def iast_bytes_aspect():
    return bytes_aspect(bytes, 0, "hello", "utf-8")  # noqa: F821


def bytes_noaspect():
    return bytes("hello", "utf-8")


def iast_bytesio_aspect():
    x = bytesio_aspect(None, 0, b"data")  # noqa: F821
    return x.read()


def bytesio_noaspect():
    x = _io.BytesIO(b"data")
    return x.read()


def iast_capitalize_aspect():
    return capitalize_aspect(str, 0, "example")  # noqa: F821


def capitalize_noaspect():
    return "example".capitalize()


def iast_casefold_aspect():
    return casefold_aspect(str, 0, "EXAMPLE")  # noqa: F821


def casefold_noaspect():
    return "EXAMPLE".casefold()


def iast_decode_aspect():
    return decode_aspect(str, 0, b"hello", "utf-8")  # noqa: F821


def decode_noaspect():
    return b"hello".decode("utf-8")


def iast_encode_aspect():
    return encode_aspect(bytes, 0, "hello", "utf-8")  # noqa: F821


def encode_noaspect():
    return "hello".encode("utf-8")


def iast_format_aspect():
    return format_aspect(None, 1, "Hello, {}!", "World")  # noqa: F821


def format_noaspect():
    return "Hello, {}!".format("World")


def iast_format_map_aspect():
    return format_map_aspect(None, 1, "{greeting}, World!", {"greeting": "Hello"})  # noqa: F821


def format_map_noaspect():
    return "{greeting}, World!".format_map({"greeting": "Hello"})


def iast_index_aspect():
    return index_aspect("example", 3)  # noqa: F821


def index_noaspect():
    return "example"[3]


def iast_join_aspect():
    return join_aspect(None, 1, ", ", ["one", "two", "three"])  # noqa: F821


def join_noaspect():
    return ", ".join(["one", "two", "three"])


def iast_lower_aspect():
    return lower_aspect(None, 1, "EXAMPLE")  # noqa: F821


def lower_noaspect():
    return "EXAMPLE".lower()


def iast_ljust_aspect():
    return ljust_aspect(None, 1, "example", 10)  # noqa: F821


def ljust_noaspect():
    return "example".ljust(10)


def iast_modulo_aspect():
    return modulo_aspect("hello %s", "foo")  # noqa: F821


def iast_modulo_aspect_for_bytes():
    return modulo_aspect(b"hello %s", b"foo")  # noqa: F821


def iast_modulo_aspect_for_bytes_bytearray():
    return modulo_aspect(b"hello %s", bytearray(b"foo"))  # noqa: F821


def iast_modulo_aspect_for_bytearray_bytearray():
    return modulo_aspect(bytearray(b"hello %s"), bytearray(b"foo"))  # noqa: F821


def modulo_noaspect():
    return "{} {}".format("hello", "world")


def iast_ospathbasename_aspect():
    return ospathbasename_aspect("/path/to/file")  # noqa: F821


def ospathbasename_noaspect():
    return os.path.basename("/path/to/file")


def iast_ospathdirname_aspect():
    return ospathdirname_aspect("/path/to/file")  # noqa: F821


def ospathdirname_noaspect():
    return os.path.dirname("/path/to/file")


def iast_ospathjoin_aspect():
    return ospathjoin_aspect("/path", "to", "file")  # noqa: F821


def ospathjoin_noaspect():
    return os.path.join("/path", "to", "file")


def iast_ospathnormcase_aspect():
    return ospathnormcase_aspect("example")  # noqa: F821


def ospathnormcase_noaspect():
    return os.path.normcase("example")


def iast_ospathsplit_aspect():
    return ospathsplit_aspect("/path/to/file")  # noqa: F821


def ospathsplit_noaspect():
    return os.path.split("/path/to/file")


def iast_ospathsplitdrive_aspect():
    return ospathsplitdrive_aspect("/path/to/file")  # noqa: F821


def ospathsplitdrive_noaspect():
    return os.path.splitdrive("/path/to/file")


def iast_ospathsplitext_aspect():
    return ospathsplitext_aspect("/path/to/file")  # noqa: F821


def ospathsplitext_noaspect():
    return os.path.splitext("/path/to/file")


def iast_re_sub_aspect():
    return re_sub_aspect(None, 1, re.compile("/"), "_", "foo/bar")  # noqa: F821


def re_sub_noaspect():
    return re.sub("/", "_", "foo/bar")


def iast_rsplit_aspect():
    return rsplit_aspect(None, 0, "foo bar baz")  # noqa: F821


def rsplit_noaspect():
    return "foo bar baz".rsplit()


def iast_splitlines_aspect():
    return splitlines_aspect(None, 0, "line1\nline2\nline3")  # noqa: F821


def splitlines_noaspect():
    return "line1\nline2\nline3".splitlines()


def iast_str_aspect():
    return str_aspect(str, 0, 42)  # noqa: F821


def str_noaspect():
    return str(42)


def iast_stringio_aspect():
    io = stringio_aspect(None, 0, "data")  # noqa: F821
    return io.read()


def stringio_noaspect():
    io = _io.StringIO("data")
    return io.read()


def iast_repr_aspect():
    return repr_aspect(None, 0, 42)  # noqa: F821


def repr_noaspect():
    return repr(42)


def iast_slice_aspect():
    return slice_aspect(  # noqa: F821
        "example",
        1,
        3,
        1,
    )


def slice_noaspect():
    return "example"[1:3:1]


def iast_replace_aspect():
    return replace_aspect(None, 1, "example", "example", "foo")  # noqa: F821


def replace_noaspect():
    return "example".replace("example", "foo")


def iast_re_subn_aspect():
    return re_subn_aspect(None, 1, re.compile("/"), "_", "foo/bar")  # noqa: F821


def re_subn_noaspect():
    return re.subn("/", "_", "foo/bar")


def iast_re_search_aspect():
    return re_search_aspect(None, 1, re.compile("foo"), "foo bar")  # noqa: F821


def re_search_noaspect():
    return re.search("foo", "foo bar")


def iast_re_match_aspect():
    return re_match_aspect(None, 1, re.compile("foo"), "foo bar")  # noqa: F821


def re_match_noaspect():
    return re.match("foo", "foo bar")


def iast_re_groups_aspect():
    return re_groups_aspect(None, 0, re.match(r"(\w+) (\w+)", "Hello World"))  # noqa: F821


def re_groups_noaspect():
    return re.match(r"(\w+) (\w+)", "Hello World").groups()


def iast_re_group_aspect():
    return re_group_aspect(None, 0, re.match(r"(\w+) (\w+)", "Hello World"))  # noqa: F821


def re_group_noaspect():
    return re.match(r"(\w+) (\w+)", "Hello World").group()


def iast_re_fullmatch_aspect():
    return re_fullmatch_aspect(None, 1, re.compile("foo"), "foo")  # noqa: F821


def re_fullmatch_noaspect():
    return re.fullmatch("foo", "foo")


def iast_re_finditer_aspect():
    return re_finditer_aspect(None, 1, re.compile("foo"), "foo bar foo")  # noqa: F821


def re_finditer_noaspect():
    return re.finditer("foo", "foo bar foo")


def iast_re_findall_aspect():
    return re_findall_aspect(None, 1, re.compile("foo"), "foo bar foo")  # noqa: F821


def re_findall_noaspect():
    return re.findall("foo", "foo bar foo")


def iast_re_expand_aspect():
    re_obj = re.compile(r"(\w+) (\w+)")
    match = re.match(re_obj, "Hello World")
    return re_expand_aspect(None, 1, match, "Salute: \\1 Subject: \\2")  # noqa: F821


def re_expand_noaspect():
    re_obj = re.compile(r"(\w+) (\w+)")
    match = re.match(re_obj, "Hello World")
    return match.expand("Salute: \\1 Subject: \\2")


def iast_upper_aspect():
    return upper_aspect(None, 1, "example")  # noqa: F821


def upper_noaspect():
    return "example".upper()


def iast_translate_aspect():
    return translate_aspect(None, 1, "example", {101: 105})  # noqa: F821


def translate_noaspect():
    return "example".translate({101: 105})


def iast_title_aspect():
    return title_aspect(None, 1, "hello world")  # noqa: F821


def title_noaspect():
    return "hello world".title()


def iast_swapcase_aspect():
    return swapcase_aspect(None, 1, "Hello World")  # noqa: F821


def swapcase_noaspect():
    return "Hello World".swapcase()


def iast_split_aspect():
    return split_aspect(None, 1, "foo bar baz")  # noqa: F821


def split_noaspect():
    return "foo bar baz".split()
