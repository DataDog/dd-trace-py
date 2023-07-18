from ddtrace.internal.compat import ensure_pep562
from ddtrace.vendor.debtcollector import deprecate


# tags
CLUSTER = "cassandra.cluster"
KEYSPACE = "cassandra.keyspace"
CONSISTENCY_LEVEL = "cassandra.consistency_level"
PAGINATED = "cassandra.paginated"
PAGE_NUMBER = "cassandra.page_number"


def __getattr__(name):
    if name == "ROW_COUNT":
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            postfix=". Use ddtrace.ext.db.ROWCOUNT instead.",
            removal_version="2.0.0",
        )
        return "cassandra.row_count"

    if name in globals():
        return globals()[name]

    raise AttributeError("'%s' has no attribute '%s'", __name__, name)


ensure_pep562(__name__)
