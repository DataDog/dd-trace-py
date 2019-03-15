from .collector import ValueCollector


class RuntimeMetricTagCollector(ValueCollector):
    periodic = False


class PlatformMetricTagCollector(RuntimeMetricTagCollector):
    """Tag collector for the Python interpreter implementation.

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
        metrics = {}
        if 'lang_interpreter' in keys:
            metrics['lang_interpreter'] = platform.python_implementation()

        if 'lang_version' in keys:
            metrics['lang_version'] = platform.python_version()
        return metrics
