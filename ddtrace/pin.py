
import logging

import ddtrace


log = logging.getLogger(__name__)


class Pin(object):
    """ Pin (a.k.a Patch INfo) is a small class which is used to
        set tracing metadata on a particular traced connection.
        This is useful if you wanted to, say, trace two different
        database clusters clusters.

        >>> conn = sqlite.connect("/tmp/user.db")
        >>> pin = Pin.get_from(conn)
        >>> if pin:
                pin.service = "user-db"
                pin.onto(conn)
        >>> conn = sqlite.connect("/tmp/image.db")
        >>> pin = Pin.get_from(conn)
        >>> if pin:
                pin.service = "image-db"
                pin.onto(conn)

    """

    @staticmethod
    def get_from(obj):
        """ Return the pin associated with the given object.

            >>> pin = Pin.get_from(conn)
        """
        if hasattr(obj, '__getddpin__'):
            return obj.__getddpin__()
        return getattr(obj, '_datadog_pin', None)

    def __init__(self, service, app=None, app_type=None, tracer=None, tags=None):
        self.service = service      # the internal name of a system
        self.app = app              # the 'product' name of a software (e.g postgres)
        self.tags = tags            # some tags on this instance.
        self.app_type = app_type    # db, web, etc

        # the name of the operation we're measuring (rarely used)
        self.name = None
        # optionally specify an alternate tracer to use. this will
        # mostly be used by tests.
        self.tracer = tracer or ddtrace.tracer

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
            log.warn("can't pin onto object", exc_info=True)

    def _send(self):
        self.tracer.set_service_info(
            service=self.service,
            app=self.app,
            app_type=self.app_type)

    def __repr__(self):
        return "Pin(service:%s,app:%s,app_type:%s,name:%s)" % (
            self.service,
            self.app,
            self.app_type,
            self.name)

