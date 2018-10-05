import wrapt
import vertica_python

from ddtrace import config
from ddtrace import Pin
from ddtrace.ext import net, AppTypes

from .constants import APP
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
        orig_cursor_fn = getattr(self.__wrapped__, "cursor")
        cursor = orig_cursor_fn(*args, **kwargs)

        if cursor:
            traced_cursor = TracedCursor(cursor)
            tags = {}
            tags[net.TARGET_HOST] = self.options["host"]
            tags[net.TARGET_PORT] = self.options["port"]

            pin = Pin(
                service=config.vertica["service_name"],
                app=APP,
                app_type=AppTypes.db,
                _config=config.vertica,
                tags=tags,
            )
            pin.onto(traced_cursor)
            return traced_cursor
        else:
            return cursor
