from sqlite3 import Connection

from ...internal.utils.deprecation import deprecated


@deprecated(message="Use patching instead (see the docs).", version="1.0.0")
def connection_factory(*args, **kwargs):
    return Connection
