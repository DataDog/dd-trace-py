import logging
import wrapt

import ddtrace

log = logging.getLogger(__name__)

_DD_PIN_NAME = '_datadog_pin'

# To set attributes on wrapt proxy objects use this prefix:
# http://wrapt.readthedocs.io/en/latest/wrappers.html
_DD_PIN_PROXY_NAME = '_self_' + _DD_PIN_NAME


class Pin(object):
    """ Pin (a.k.a Patch INfo) is a small class which is used to
        set tracing metadata on a particular traced connection.
        This is useful if you wanted to, say, trace two different
        database clusters.

        >>> conn = sqlite.connect("/tmp/user.db")
        >>> # Override a pin for a specific connection
        >>> pin = Pin.override(conn, service="user-db")
        >>> conn = sqlite.connect("/tmp/image.db")
    """

    __slots__ = ['app', 'app_type', 'service', 'tags', 'tracer', '_initialized']

    def __init__(self, service, app=None, app_type=None, tags=None, tracer=None):
        tracer = tracer or ddtrace.tracer
        self.service = service
        self.app = app
        self.app_type = app_type
        self.tags = tags
        self.tracer = tracer
        self._initialized = True

    def __setattr__(self, name, value):
        if hasattr(self, '_initialized'):
            raise AttributeError("can't mutate a pin, use override() or clone() instead")
        super(Pin, self).__setattr__(name, value)

    def __repr__(self):
        return "Pin(service=%s, app=%s, app_type=%s, tags=%s, tracer=%s)" % (
            self.service, self.app, self.app_type, self.tags, self.tracer)

    @staticmethod
    def get_from(obj):
        """ Return the pin associated with the given object.

            >>> pin = Pin.get_from(conn)
        """
        if hasattr(obj, '__getddpin__'):
            return obj.__getddpin__()

        pin_name = _DD_PIN_PROXY_NAME if isinstance(obj, wrapt.ObjectProxy) else _DD_PIN_NAME
        return getattr(obj, pin_name, None)

    @classmethod
    def override(cls, obj, service=None, app=None, app_type=None, tags=None, tracer=None):
        """Override an object with the given attributes.

        That's the recommended way to customize an already instrumented client, without
        losing existing attributes.

        >>> conn = sqlite.connect("/tmp/user.db")
        >>> # Override a pin for a specific connection
        >>> pin = Pin.override(conn, service="user-db")
        """
        if not obj:
            return

        pin = cls.get_from(obj)
        if not pin:
            pin = Pin(service)

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
                log.debug("can't send pin info", exc_info=True)

        # Actually patch it on the object.
        try:
            if hasattr(obj, '__setddpin__'):
                return obj.__setddpin__(self)

            pin_name = _DD_PIN_PROXY_NAME if isinstance(obj, wrapt.ObjectProxy) else _DD_PIN_NAME
            return setattr(obj, pin_name, self)
        except AttributeError:
            log.debug("can't pin onto object. skipping", exc_info=True)

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
