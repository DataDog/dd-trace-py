from sqlite3 import Connection

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ...vendor.debtcollector.removals import remove


@remove(message="Use patching instead (see the docs).", category=DDTraceDeprecationWarning, removal_version="1.0.0")
def connection_factory(*args, **kwargs):
    return Connection
