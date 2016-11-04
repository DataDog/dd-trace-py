
import ddtrace


class ServiceInfo(object):

    @classmethod
    def get_from(self, obj):
        return getattr(obj, '_datadog_service_info', None)

    def __init__(self, service, app=None, tracer=None):
        self.service = service
        self._app = app
        self._tracer = tracer

    def enabled(self):
        return self.tracer().enabled

    def tracer(self):
        if self._tracer:
            return self._tracer
        return ddtrace.tracer

    def set_on(self, obj):
        return setattr(obj, '_datadog_service_info', self)


