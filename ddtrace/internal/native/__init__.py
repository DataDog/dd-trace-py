import os
import sys
from typing import Dict

from ._native import DDSketch  # noqa: F401
from ._native import PyConfigurator


def get_configuration_from_disk(debug_logs: bool = False, file_override="") -> Dict[str, str]:
    """
    Retrieves the tracer configuration from disk. Calls the PyConfigurator object
    to read the configuration from the disk using the libdatadog shared library
    and returns the corresponding configuration
    """
    configurator = PyConfigurator(debug_logs)
    configurator.set_envp(["%s=%s" % (k, v) for k, v in os.environ.items()])
    configurator.set_args(sys.argv)

    # Set the file override if provided. Only used for testing purposes.
    if file_override:
        configurator.set_file_override(file_override)

    return configurator.get_configuration()
