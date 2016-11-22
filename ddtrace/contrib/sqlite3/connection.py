from sqlite3 import Connection

from ddtrace.util import deprecated

@deprecated(message='Use patching instead (see the docs).', version='0.6.0')
def connection_factory(*args, **kwargs):
    return Connection
