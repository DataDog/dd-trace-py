"""
Nameko Integration

Usage: Add ddtrace.contrib.nameko.DDTracer dependency provider to your service


from nameko.rpc import rpc
from ddtrace.contrib.nameko import DDTracer


class Service:

    name = 'service-name'

    tracer = Tracer()

    @rpc
    def ping(self, payload):
        return 'ping, {}!'.format(str(payload))



"""
from ..util import require_modules

required_modules = ['nameko']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .provider import DDTracer

        __all__ = [
            'DDTracer',
        ]
