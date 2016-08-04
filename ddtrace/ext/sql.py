
# the type of the spans
TYPE = "sql"

# tags
QUERY = "sql.query"   # the query text
ROWS = "sql.rows"     # number of rows returned by a query
DB = "sql.db"         # the name of the database


def normalize_vendor(vendor):
    """ Return a canonical name for a type of database. """
    if not vendor:
        return "db"  # should this ever happen?
    elif vendor == "sqlite":
        return "sqlite3"  # for consistency with the sqlite3 integration
    elif vendor == "postgresql":
        return "postgres"
    else:
        return vendor
