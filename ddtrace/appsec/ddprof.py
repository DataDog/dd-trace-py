import os
import ctypes
from ctypes import c_int

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


class PyDDProfException(Exception):
    pass


ddprof_lib_path = os.path.join(os.path.dirname(__file__), "ddprof_lib", "lib", "libdd_profiling.so")
if not os.path.exists(ddprof_lib_path):
    raise PyDDProfException(f"ddprof library not found at {ddprof_lib_path}")

for required_env in {"DD_ENV", "DD_SERVICE", "DD_API_KEY"}:
    if required_env not in os.environ:
        raise PyDDProfException(f"Environment variable required for DDProf {required_env} is not set")

ddprof = ctypes.CDLL(ddprof_lib_path)

ddprof.ddprof_start_profiling.argtypes = []
ddprof.ddprof_start_profiling.restype = None

ddprof.ddprof_stop_profiling.argtypes = [c_int]
ddprof.ddprof_stop_profiling.restype = None


def start_profiling():
    ddprof.ddprof_start_profiling()


def stop_profiling(timeout: int):
    ddprof.ddprof_stop_profiling(timeout)
