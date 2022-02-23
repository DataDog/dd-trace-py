from sqlite3 import Connection

from ...vendor.debtcollector.removals import remove


@remove(message="Use patching instead (see the docs).", removal_version="1.0.0")
def connection_factory(*args, **kwargs):
    return Connection
