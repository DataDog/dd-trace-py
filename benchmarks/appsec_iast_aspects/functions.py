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


def iast_add_inplace_aspect(tainted_str="test"):
    return add_inplace_aspect(tainted_str, "second_string")  # noqa: F821


def iast_bytearray_aspect(tainted_ba=b"test"):
    return bytearray_aspect(bytearray, 0, tainted_ba)  # noqa: F821


def iast_bytearray_extend_aspect(tainted_ba=b"test"):
    ba = bytearray(tainted_ba)
    bytearray_extend_aspect(None, 0, ba, b" world")  # noqa: F821
    return ba


def iast_bytes_aspect(tainted_str="test"):
    return bytes_aspect(bytes, 0, tainted_str, "utf-8")  # noqa: F821


def iast_bytesio_aspect(tainted_b=b"data"):
    x = bytesio_aspect(None, 0, tainted_b)  # noqa: F821
    return x.read()


def iast_capitalize_aspect(tainted_str="example"):
    return capitalize_aspect(str, 0, tainted_str)  # noqa: F821


def iast_casefold_aspect(tainted_str="EXAMPLE"):
    return casefold_aspect(str, 0, tainted_str)  # noqa: F821


def iast_decode_aspect(tainted_b=b"data"):
    return decode_aspect(str, 0, tainted_b, "utf-8")  # noqa: F821


def iast_encode_aspect(tainted_str="hello"):
    return encode_aspect(bytes, 0, tainted_str, "utf-8")  # noqa: F821


def iast_format_aspect(tainted_str="Hello, {}!"):
    return format_aspect(None, 1, tainted_str, "World")  # noqa: F821


def iast_format_map_aspect(tainted_str="Hello"):
    return format_map_aspect(None, 1, "{greeting}, World!", {"greeting": tainted_str})  # noqa: F821


def iast_index_aspect(tainted_str="example"):
    return index_aspect(tainted_str, 3)  # noqa: F821


def iast_join_aspect(tainted_str="one"):
    return join_aspect(None, 1, ", ", [tainted_str, "two", "three"])  # noqa: F821


def iast_lower_aspect(tainted_str="EXAMPLE"):
    return lower_aspect(None, 1, tainted_str)  # noqa: F821


def iast_ljust_aspect(tainted_str="example"):
    return ljust_aspect(None, 1, tainted_str, 10)  # noqa: F821


def iast_modulo_aspect(tainted_str="example"):
    return modulo_aspect("hello %s", tainted_str)  # noqa: F821


def iast_modulo_aspect_for_bytes(tainted_bytes=b"example"):
    return modulo_aspect(b"hello %s", tainted_bytes)  # noqa: F821


def iast_modulo_aspect_for_bytes_bytearray(tainted_bytes=b"hello %s"):
    return modulo_aspect(tainted_bytes, bytearray(b"foo"))  # noqa: F821


def iast_modulo_aspect_for_bytearray_bytearray(tainted_bytearray=bytearray(b"hello %s")):
    return modulo_aspect(tainted_bytearray, bytearray(b"foo"))  # noqa: F821


def iast_str_aspect(tainted_str="example"):
    return str_aspect(str, 0, tainted_str)  # noqa: F821


def iast_stringio_aspect(tainted_str="example"):
    io = stringio_aspect(None, 0, tainted_str)  # noqa: F821
    return io.read()


def iast_repr_aspect(tainted_str="example"):
    return repr_aspect(None, 0, tainted_str)  # noqa: F821


def iast_slice_aspect(tainted_str="example"):
    return slice_aspect(  # noqa: F821
        tainted_str,
        1,
        3,
        1,
    )


def iast_replace_aspect(tainted_str="example"):
    return replace_aspect(None, 1, tainted_str, "example", "foo")  # noqa: F821


def iast_upper_aspect(tainted_str="example"):
    return upper_aspect(None, 1, tainted_str)  # noqa: F821


def iast_translate_aspect(tainted_str="example"):
    return translate_aspect(None, 1, tainted_str, {101: 105})  # noqa: F821


def iast_title_aspect(tainted_str="hello world"):
    return title_aspect(None, 1, tainted_str)  # noqa: F821


def iast_swapcase_aspect(tainted_str="Hello World"):
    return swapcase_aspect(None, 1, tainted_str)  # noqa: F821


def iast_strip_aspect(tainted_str="    foo bar baz    "):
    return strip_aspect(None, 1, tainted_str)  # noqa: F821


def iast_rstrip_aspect(tainted_str="    foo bar baz    "):
    return rstrip_aspect(None, 1, tainted_str)  # noqa: F821


def iast_lstrip_aspect(tainted_str="    foo bar baz    "):
    return lstrip_aspect(None, 1, tainted_str)  # noqa: F821
