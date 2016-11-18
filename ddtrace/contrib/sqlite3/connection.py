from sqlite3 import Connection


def connection_factory(*args, **kwargs):
    # DEPRECATED
    return Connection
