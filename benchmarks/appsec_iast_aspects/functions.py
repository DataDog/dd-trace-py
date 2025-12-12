import _io


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
    return bytearray(tainted_ba)


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


def iast_format_map_aspect(tainted_str="Hello"):
    return format_map_aspect(None, 1, "{greeting}, World!", {"greeting": tainted_str})  # noqa: F821


def format_map_noaspect(tainted_str="Hello"):
    return "{greeting}, World!".format_map({"greeting": tainted_str})


def iast_index_aspect(tainted_str="example"):
    return index_aspect(tainted_str, 3)  # noqa: F821


def index_noaspect(tainted_str="example"):
    return tainted_str[3]


def iast_join_aspect(tainted_str="one"):
    return join_aspect(None, 1, ", ", [tainted_str, "two", "three"])  # noqa: F821


def join_noaspect(tainted_str="one", **kwargs):
    return ", ".join([tainted_str, "two", "three"])


def iast_lower_aspect(tainted_str="EXAMPLE"):
    return lower_aspect(None, 1, tainted_str)  # noqa: F821


def lower_noaspect(tainted_str="EXAMPLE"):
    return tainted_str.lower()


def iast_ljust_aspect(tainted_str="example"):
    return ljust_aspect(None, 1, tainted_str, 10)  # noqa: F821


def ljust_noaspect(tainted_str="example"):
    return tainted_str.ljust(10)


def iast_modulo_aspect(tainted_str="example"):
    return modulo_aspect("hello %s", tainted_str)  # noqa: F821


def iast_modulo_aspect_for_bytes(tainted_bytes=b"example"):
    return modulo_aspect(b"hello %s", tainted_bytes)  # noqa: F821


def iast_modulo_aspect_for_bytes_bytearray(tainted_bytes=b"hello %s"):
    return modulo_aspect(tainted_bytes, bytearray(b"foo"))  # noqa: F821


def iast_modulo_aspect_for_bytearray_bytearray(tainted_bytearray=bytearray(b"hello %s")):
    return modulo_aspect(tainted_bytearray, bytearray(b"foo"))  # noqa: F821


def modulo_noaspect(tainted_str="example"):
    return "{} {}".format(tainted_str, "world")


def iast_str_aspect(tainted_str="example"):
    return str_aspect(str, 0, tainted_str)  # noqa: F821


def str_noaspect(tainted_str="example"):
    return str(tainted_str)


def iast_stringio_aspect(tainted_str="example"):
    io = stringio_aspect(None, 0, tainted_str)  # noqa: F821
    return io.read()


def stringio_noaspect(tainted_str="example"):
    io = _io.StringIO(tainted_str)
    return io.read()


def iast_repr_aspect(tainted_str="example"):
    return repr_aspect(None, 0, tainted_str)  # noqa: F821


def repr_noaspect(tainted_str="example"):
    return repr(tainted_str)


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
