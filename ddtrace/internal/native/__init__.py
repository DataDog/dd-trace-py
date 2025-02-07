import os
from typing import Dict

from ._native import DDSketch  # noqa: F401
from ._native import PyConfigurator


def get_configuration_from_disk(debug_logs: bool = False, file_override="") -> Dict[str, str]:
    """
    Retrieves the tracer configuration from disk. Calls the PyConfigurator object
    to read the configuration from the disk using the libdatadog shared library
    and returns the corresponding configuration
    See https://github.com/DataDog/libdatadog/blob/06d2b6a19d7ec9f41b3bfd4ddf521585c55298f6/library-config/src/lib.rs
    for more information on how the configuration is read from disk
    """
    configurator = PyConfigurator(debug_logs)

    # Set the file override if provided. Only used for testing purposes.
    if file_override:
        configurator.set_local_file_override(file_override)

    return configurator.get_configuration()


def _apply_configuration_from_disk():
    """
    Sets the configuration from disk as environment variables.
    This is not ideal and we should consider a better mechanism to
    apply this configuration to the tracer.
    Currently here is the order of precedence (higher takes precedence):
    1. Dynamic remote configuration
    2. Runtime configuration (ie fields set manually by customers / from the ddtrace code)
    3. Configuration from disk
    4. Environment variables
    5. Default values
    """
    for key, value in get_configuration_from_disk().items():
        os.environ[key] = str(value).lower()
