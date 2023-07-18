from ddtrace.internal.compat import ensure_pep562
from ddtrace.vendor.debtcollector import deprecate


SERVICE = "mongodb"
COLLECTION = "mongodb.collection"
DB = "mongodb.db"
ROWS = "mongodb.rows"
QUERY = "mongodb.query"


def __getattr__(name):
    if name == "ROWS":
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            postfix=". Use ddtrace.ext.db.ROWCOUNT instead.",
            removal_version="2.0.0",
        )
        return "mongodb.rows"

    if name in globals():
        return globals()[name]

    raise AttributeError("'%s' has no attribute '%s'", __name__, name)


ensure_pep562(__name__)
