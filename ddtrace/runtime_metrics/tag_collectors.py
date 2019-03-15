from .collector import ValueCollector
from ddtrace import __version__


class RuntimeMetricTagCollector(ValueCollector):
    periodic = False


class PlatformTagCollector(RuntimeMetricTagCollector):
    """Tag collector for the Python interpreter implementation.

    Tag collected:
    - lang_interpreter:
      - For CPython this is 'CPython'.
      - For Pypy this is 'PyPy'.
      - For Jython this is 'Jython'.
    """
    required_modules = ['platform']

    def collect_fn(self, keys):
        platform = self.modules.get('platform')
        metrics = {}
        if 'lang_interpreter' in keys:
            metrics['lang_interpreter'] = platform.python_implementation()

        if 'lang_version' in keys:
            metrics['lang_version'] = platform.python_version()
        return metrics


class TracerVersionTagCollector(RuntimeMetricTagCollector):
    def collect_fn(self, keys):
        if 'version' not in keys:
            return {}
        return {
            'version': __version__
        }

