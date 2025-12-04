# A local 'path' module that should NOT be incorrectly matched
# when importing `from os import path`

LOCAL_PATH_CONSTANT = "I am local path module"


def local_path_function():
    return LOCAL_PATH_CONSTANT
