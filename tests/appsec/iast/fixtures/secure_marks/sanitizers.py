import shlex


def shlex_quote(tainted_string):
    return shlex.quote(tainted_string)


def shlex_quote_move_ranges(tainted_string):
    return "12345-no-tainted" + shlex.quote(tainted_string)
