import os
import sys
from subprocess import Popen

from ddtrace import config
from ..compat import PYTHON_VERSION_INFO
from ..logger import get_logger


log = get_logger(__name__)


def maybe_start_serverless_mini_agent():
    if not (config._is_gcp_function or config._is_azure_function_consumption_plan):
        return

    if sys.platform != "win32" and sys.platform != "linux":
        log.error("Serverless Mini Agent is only supported on Windows and Linux.")
        return

    try:
        rust_binary_path = get_rust_binary_path()

        log.debug("Trying to spawn the Serverless Mini Agent at path: {}".format(rust_binary_path))
        Popen(rust_binary_path)
    except Exception as e:
        log.error("Error spawning Serverless Mini Agent process: {}".format(repr(e)))


def get_rust_binary_path():
    rust_binary_path = os.getenv("DD_MINI_AGENT_PATH")

    if rust_binary_path is not None:
        return rust_binary_path

    if config._is_gcp_function:
        python_folder_name = "python%d.%d" % PYTHON_VERSION_INFO[:2]
        rust_binary_path = (
            "/layers/google.python.pip/pip/lib/{}/site-packages/"
            "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        ).format(python_folder_name)
    else:
        # python is only supported on Linux Azure Functions
        rust_binary_path = (
            "/home/site/wwwroot/.python_packages/lib/site-packages/"
            "datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        )

    return rust_binary_path
