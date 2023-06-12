from typing import List
from typing import Tuple

from ...constants import ENV_KEY
from .collector import ValueCollector
from .constants import LANG
from .constants import LANG_INTERPRETER
from .constants import LANG_VERSION
from .constants import SERVICE
from .constants import TRACER_VERSION


class RuntimeTagCollector(ValueCollector):
    periodic = False
    value = []  # type: List[Tuple[str, str]]


class TracerTagCollector(RuntimeTagCollector):
    """Tag collector for the ddtrace Tracer"""

    required_modules = ["ddtrace"]

    def collect_fn(self, keys):
        ddtrace = self.modules.get("ddtrace")
        # make sure to copy _services to avoid RuntimeError: Set changed size during iteration
        tags = [(SERVICE, service) for service in list(ddtrace.tracer._services)]
        if ENV_KEY in ddtrace.tracer._tags:
            tags.append((ENV_KEY, ddtrace.tracer._tags.get(ENV_KEY)))
        return tags


class PlatformTagCollector(RuntimeTagCollector):
    """Tag collector for the Python interpreter implementation.

    Tags collected:
    - ``lang_interpreter``:

      * For CPython this is 'CPython'.
      * For Pypy this is ``PyPy``
      * For Jython this is ``Jython``

    - `lang_version``,  eg ``2.7.10``
    - ``lang`` e.g. ``Python``
    - ``tracer_version`` e.g. ``0.29.0``

    """

    required_modules = ["platform", "ddtrace"]

    def collect_fn(self, keys):
        platform = self.modules.get("platform")
        ddtrace = self.modules.get("ddtrace")
        tags = [
            (LANG, "python"),
            (LANG_INTERPRETER, platform.python_implementation()),
            (LANG_VERSION, platform.python_version()),
            (TRACER_VERSION, ddtrace.__version__),
        ]
        return tags
