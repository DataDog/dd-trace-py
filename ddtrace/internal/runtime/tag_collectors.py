from .collector import ValueCollector
from .constants import (
    RUNTIME_ID,
    SERVICE,
    LANG_INTERPRETER,
    LANG_VERSION,
)


class RuntimeTagCollector(ValueCollector):
    periodic = False
    value = []


class TracerTagCollector(RuntimeTagCollector):
    """ Tag collector for the ddtrace Tracer
    """
    required_modules = ['ddtrace']

    def collect_fn(self, keys):
        ddtrace = self.modules.get('ddtrace')
        tags = [(RUNTIME_ID, ddtrace.tracer._runtime_id)]
        tags += [(SERVICE, service) for service in ddtrace.tracer._services]
        return tags


class PlatformTagCollector(RuntimeTagCollector):
    """ Tag collector for the Python interpreter implementation.

    Tags collected:
    - lang_interpreter:
      - For CPython this is 'CPython'.
      - For Pypy this is 'PyPy'.
      - For Jython this is 'Jython'.
    - lang_version:
      - eg. '2.7.10'
    """
    required_modules = ['platform']

    def collect_fn(self, keys):
        platform = self.modules.get('platform')
        tags = [
            (LANG_INTERPRETER, platform.python_implementation()),
            (LANG_VERSION, platform.python_version())
        ]
        return tags
