from collections import namedtuple


LineNo = namedtuple("LineNo", ["create", "acquire", "release"])
lock_locs = {}
loc_type_map = {
    "!CREATE!": "create",
    "!ACQUIRE!": "acquire",
    "!RELEASE!": "release",
}


def get_lock_locations(path: str):
    """
    The lock profiler is capable of determining where locks are created and used. In order to test this behavior, line
    numbers are compared in several tests. However, since it's cumbersome to write the tests in any way except
    inline, this means line numbers are volatile over the development of this file. Instead of hardcoding line
    numbers, we add a comment at each interesting point in code, then process this file to find the line numbers. This
    is certainly brittle in some way, but it's better than shuffling line numbers around forever.
    """
    global lock_locs

    # Lock lookups need a level of indirection since we're processing this very same file for sentinel strings
    loc_types = ["!CREATE!", "!ACQUIRE!", "!RELEASE!"]
    start_line = 50  # No clever tricks, just avoid processing this function
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            if lineno < start_line:
                continue
            for loc_type in loc_types:
                if loc_type in line:
                    lock_name = line.split(" ")[-1].strip()
                    if lock_name not in lock_locs:
                        lock_locs[lock_name] = LineNo(0, 0, 0)
                    field = loc_type_map[loc_type]
                    lock_locs[lock_name] = lock_locs[lock_name]._replace(**{field: lineno})


def get_lock_linenos(name):
    return lock_locs.get(name, LineNo(0, 0, 0))


def init_linenos(path):
    get_lock_locations(path)
