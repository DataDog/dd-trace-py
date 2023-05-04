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
        python_folder_name = "python%d.%d" % PYTHON_VERSION_INFO[:2]

        rust_binary_path_default = (
            "/layers/google.python.pip/pip/lib/%s/site-packages/"
            "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        ) % python_folder_name

        rust_binary_path = os.getenv("DD_MINI_AGENT_PATH", rust_binary_path_default)

        log.debug("rust_binary_path: %s", rust_binary_path)
        Popen(rust_binary_path)
    except Exception as e:
        log.error("Error spawning Serverless Mini Agent process: %s", repr(e))
