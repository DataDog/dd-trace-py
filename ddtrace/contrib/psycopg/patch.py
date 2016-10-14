
import logging

import wrapt
import psycopg2
from psycopg2.extensions import connection, cursor


log = logging.getLogger(__name__)



class TracedCursor(wrapt.ObjectProxy):

    _service = None
    _tracer = None

    def __init__(self, cursor, service, tracer):
        super(TracedCursor, self).__init__(cursor)
        self._service = service
        self._tracer = tracer

    def execute(self, *args, **kwargs):
        log.info("exec %s", self._service)
        return self.__wrapped__.execute(*args, **kwargs)


class TracedConnection(wrapt.ObjectProxy):

    datadog_service = "postgres"
    datadog_tracer = None

    def cursor(self, *args, **kwargs):
        cursor = self.__wrapped__.cursor(*args, **kwargs)
        return TracedCursor(cursor, self.datadog_service, None)


def _connect(connect_func, _, args, kwargs):
    db = connect_func(*args, **kwargs)
    return TracedConnection(db)

def patch():
    wrapt.wrap_function_wrapper('psycopg2', 'connect', _connect)

if __name__ == '__main__':
    import sys
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


    patch()

    db = psycopg2.connect(host='localhost', dbname='dogdata', user='dog')
    setattr(db, "datadog_service", "foo")

    cur = db.cursor()
    cur.execute("select 'foobar'")
    print cur.fetchall()

