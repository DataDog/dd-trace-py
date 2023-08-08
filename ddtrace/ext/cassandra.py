from ddtrace.internal.compat import ensure_pep562


# tags
CLUSTER = "cassandra.cluster"
KEYSPACE = "cassandra.keyspace"
CONSISTENCY_LEVEL = "cassandra.consistency_level"
PAGINATED = "cassandra.paginated"
PAGE_NUMBER = "cassandra.page_number"


def __getattr__(name):
    if name in globals():
        return globals()[name]

    raise AttributeError("'%s' has no attribute '%s'", __name__, name)


ensure_pep562(__name__)
