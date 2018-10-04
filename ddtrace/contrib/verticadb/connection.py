import wrapt
import vertica_python


from .cursor import TracedVerticaCursor as TracedCursor


_Connection = vertica_python.Connection


class TracedVerticaConnection(wrapt.ObjectProxy):
    def __init__(self, *args, **kwargs):
        conn = _Connection(kwargs)
        super(TracedVerticaConnection, self).__init__(conn)

    def cursor(self, *args, **kwargs):
        """Returns a TracedCursor proxy to a cursor if one is returned.

        Note that this is necessary to do here since vertica_python uses a
        `from .cursor import Cursor` reference which we cannot modify at
        run-time.
        """
        orig_cursor_fn = getattr(self.__wrapped__, 'cursor')
        cursor = orig_cursor_fn(*args, **kwargs)

        if cursor:
            return TracedCursor(cursor)
        else:
            return cursor
