# A local utils module that should NOT be incorrectly matched
# when importing from a package (e.g., `from somepackage import utils`)

LOCAL_UTILS_CONSTANT = "I am local utils"


def local_utils_function():
    return LOCAL_UTILS_CONSTANT
