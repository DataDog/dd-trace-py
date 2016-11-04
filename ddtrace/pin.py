
import ddtrace


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
        self.tracer = tracer or ddtrace.tracer
        self.app = app      # the 'product' name of a software
        self.name = None    # very occasionally needed
        self.tags = tags

    def enabled(self):
        """ Return true if this pin's tracer is enabled. """
        return self.tracer.enabled

    def onto(self, obj):
        """ Patch this pin onto the given object. """
        return setattr(obj, '_datadog_pin', self)
