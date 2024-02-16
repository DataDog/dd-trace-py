import builtins as _builtins


def extend(*args, **kwargs):
    return "--".join(("extend", _builtins.str(args), _builtins.str(kwargs)))


def encode(*args, **kwargs):
    return "--".join(("encode", _builtins.str(args), _builtins.str(kwargs)))


def decode(*args, **kwargs):
    return "--".join(("decode", _builtins.str(args), _builtins.str(kwargs)))


def str(*args, **kwargs):  # noqa: A001
    return "--".join(("_builtins.str", _builtins.str(args), _builtins.str(kwargs)))


def bytes(*args, **kwargs):  # noqa: A001
    return "--".join(("bytes", _builtins.str(args), _builtins.str(kwargs)))


def bytearray(*args, **kwargs):  # noqa: A001
    return "--".join(("bytearray", _builtins.str(args), _builtins.str(kwargs)))


def join(*args, **kwargs):
    return "--".join(("join", _builtins.str(args), _builtins.str(kwargs)))


def ljust(*args, **kwargs):
    return "--".join(("ljust", _builtins.str(args), _builtins.str(kwargs)))


def zfill(*args, **kwargs):
    return "--".join(("zfill", _builtins.str(args), _builtins.str(kwargs)))


def format(*args, **kwargs):  # noqa: A001
    return "--".join(("format", _builtins.str(args), _builtins.str(kwargs)))


def format_map(*args, **kwargs):
    return "--".join(("format_map", _builtins.str(args), _builtins.str(kwargs)))


def repr(*args, **kwargs):  # noqa: A001
    return "--".join(("repr", _builtins.str(args), _builtins.str(kwargs)))


def upper(*args, **kwargs):
    return "--".join(("upper", _builtins.str(args), _builtins.str(kwargs)))


def lower(*args, **kwargs):
    return "--".join(("lower", _builtins.str(args), _builtins.str(kwargs)))


def swapcase(*args, **kwargs):
    return "--".join(("swapcase", _builtins.str(args), _builtins.str(kwargs)))


def title(*args, **kwargs):
    return "--".join(("title", _builtins.str(args), _builtins.str(kwargs)))


def capitalize(*args, **kwargs):
    return "--".join(("capitalize", _builtins.str(args), _builtins.str(kwargs)))


def casefold(*args, **kwargs):
    return "--".join(("casefold", _builtins.str(args), _builtins.str(kwargs)))


def translate(*args, **kwargs):
    return "--".join(("translate", _builtins.str(args), _builtins.str(kwargs)))
