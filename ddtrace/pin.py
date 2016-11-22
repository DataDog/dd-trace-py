
from collections import namedtuple
import logging

import ddtrace


log = logging.getLogger(__name__)


_pin = namedtuple('_pin', [
    'service',
    'app',
    'app_type',
    'tags',
    'tracer'])


class Pin(_pin):
    """ Pin (a.k.a Patch INfo) is a small class which is used to
        set tracing metadata on a particular traced connection.
        This is useful if you wanted to, say, trace two different
        database clusters clusters.

        >>> conn = sqlite.connect("/tmp/user.db")
        >>> pin = Pin.get_from(conn)
        >>> if pin:
                pin.copy(service="user-db").onto(conn)
        >>> conn = sqlite.connect("/tmp/image.db")
        >>> pin = Pin.get_from(conn)
        >>> if pin:
                pin.copy(service="image-db").onto(conn)
    """

    @staticmethod
    def new(service, app=None, app_type=None, tags=None, tracer=None):
        """ Return a new pin. Convience funtion with sane defaults. """
        tracer = tracer or ddtrace.tracer
        return Pin(
            service=service,
            app=app,
            app_type=app_type,
            tags=tags,
            tracer=tracer)

    @staticmethod
    def get_from(obj):
        """ Return the pin associated with the given object.

            >>> pin = Pin.get_from(conn)
        """
        if hasattr(obj, '__getddpin__'):
            return obj.__getddpin__()
        return getattr(obj, '_datadog_pin', None)

    @classmethod
    def override(cls, obj, service=None, app=None, app_type=None, tags=None, tracer=None):
        if not obj:
            return

        pin = cls.get_from(obj)
        if pin:
            pin.clone(
                service=service,
                app=app,
                app_type=app_type,
                tags=tags,
                tracer=tracer).onto(obj)

    def enabled(self):
        """ Return true if this pin's tracer is enabled. """
        return bool(self.tracer) and self.tracer.enabled

    def onto(self, obj, send=True):
        """ Patch this pin onto the given object. If send is true, it will also
            queue the metadata to be sent to the server.
        """
        # pinning will also queue the metadata for service submission. this
        # feels a bit side-effecty, but bc it's async and pretty clearly
        # communicates what we want, i think it makes sense.
        if send:
            try:
                self._send()
            except Exception:
                log.warn("can't send pin info", exc_info=True)

        # Actually patch it on the object.
        try:
            if hasattr(obj, '__setddpin__'):
                return obj.__setddpin__(self)
            return setattr(obj, '_datadog_pin', self)
        except AttributeError:
            log.warn("can't pin onto object. skipping", exc_info=True)

    def clone(self, service=None, app=None, app_type=None, tags=None, tracer=None):
        """ Return a clone of the pin with the given attributes replaced. """
        if not tags and self.tags:
            # do a shallow copy of the tags if needed.
            tags = {k:v for k, v in self.tags.items()}

        return Pin(
            service=service or self.service,
            app=app or self.app,
            app_type=app_type or self.app_type,
            tags=tags,
            tracer=tracer or self.tracer) # no copy of the tracer

    def _send(self):
        self.tracer.set_service_info(
            service=self.service,
            app=self.app,
            app_type=self.app_type)
