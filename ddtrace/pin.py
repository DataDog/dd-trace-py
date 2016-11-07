
import logging

import ddtrace


log = logging.getLogger(__name__)


class Pin(object):
    """ Pin (a.k.a Patch INfo) is a small class which is stores
        tracer information particular to traced objects.

        >>> db = sqlite.connect(":memory:")
        >>> Pin(service="my-sqlite-service").onto(db)
    """

    @staticmethod
    def get_from(obj):
        """ Return the pin associated with the given object. """
        return getattr(obj, '_datadog_pin', None)

    def __init__(self, service, app=None, tracer=None, tags=None):
        self.service = service
        self.app = app      # the 'product' name of a software
        self.name = None    # very occasionally needed
        self.tags = tags

        # optionally specify an alternate tracer to use. this will
        # mostly be used by tests.
        self.tracer = tracer or ddtrace.tracer

    def enabled(self):
        """ Return true if this pin's tracer is enabled. """
        return bool(self.tracer) and self.tracer.enabled

    def onto(self, obj):
        """ Patch this pin onto the given object. """
        try:
            return setattr(obj, '_datadog_pin', self)
        except AttributeError:
            log.warn("can't pin onto object", exc_info=True)

    def __repr__(self):
        return "Pin(service:%s,app:%s,name:%s)" % (
            self.service,
            self.app,
            self.name)
