import importlib
import sys

from ddtrace.utils import deprecation


deprecation.deprecation("ddtrace.profile", "Use ddtrace.profiling instead.")

sys.modules[__name__] = importlib.import_module(__name__.replace("ddtrace.profile", "ddtrace.profiling"))
