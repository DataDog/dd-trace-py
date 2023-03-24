import os
from subprocess import Popen

from . import in_gcp_function
from ..compat import PYTHON_VERSION_INFO
from ..logger import get_logger


log = get_logger(__name__)


def maybe_start_serverless_mini_agent():
    if not in_gcp_function():
        return

    try:
        rust_binary_path = os.getenv("DD_MINI_AGENT_PATH", "/layers/google.python.pip/pip/lib/python%d.%d/site-packages/datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent" % PYTHON_VERSION_INFO[:2])
        log.debug("rust_binary_path: %s", rust_binary_path)
        Popen(rust_binary_path)
    except Exception as e:
        log.error("Error spawning Serverless Mini Agent process: %s", repr(e))
