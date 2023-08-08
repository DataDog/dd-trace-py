from ddtrace.internal.compat import ensure_pep562


SERVICE = "mongodb"
COLLECTION = "mongodb.collection"
DB = "mongodb.db"
ROWS = "mongodb.rows"
QUERY = "mongodb.query"


def __getattr__(name):
    if name in globals():
        return globals()[name]

    raise AttributeError("'%s' has no attribute '%s'", __name__, name)


ensure_pep562(__name__)
