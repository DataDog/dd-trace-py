import os


def from_env(name, default, value_type):
    return lambda: value_type(os.environ.get(name, default))
