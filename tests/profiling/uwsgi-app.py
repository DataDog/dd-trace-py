import os


if os.getenv("__DD_TEST_DONT_RAISE") is None:
    raise RuntimeError()


def application():
    pass
