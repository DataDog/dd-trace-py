MAJOR = 2
MINOR = 2
PATCH = 0

# must be empty for regular
# LABELS = ""

# use rcN for release candidate
# LABELS = "rc1"

# use pre.dev0 for trunk branch
LABELS = "pre.dev0"


VERSION = f"{MAJOR}.{MINOR}.{PATCH}{LABELS}"


def get_version():
    # type: () -> str
    return VERSION
