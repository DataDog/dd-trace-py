import os
import sys
from typing import Dict
from typing import List

from ddtrace.internal.utils.formats import asbool

from ._native import DDSketch  # noqa: F401
from ._native import PyConfigurator


def get_configuration_from_disk(
    debug_logs: bool = False, local_file_override="", managed_file_override=""
) -> List[Dict[str, str]]:
    """
    Retrieves the tracer configuration from disk. Calls the PyConfigurator object
    to read the configuration from the disk using the libdatadog shared library
    and returns the corresponding configuration
    See https://github.com/DataDog/libdatadog/blob/06d2b6a19d7ec9f41b3bfd4ddf521585c55298f6/library-config/src/lib.rs
    for more information on how the configuration is read from disk
    """
    configurator = PyConfigurator(debug_logs)

    # Set the file override if provided. Only used for testing purposes.
    if local_file_override:
        configurator.set_local_file_override(local_file_override)
    if managed_file_override:
        configurator.set_managed_file_override(managed_file_override)

    config = []
    try:
        config = configurator.get_configuration()
    except Exception as e:
        # No logger at this point, so we rely on good old print
        if asbool(os.environ.get("DD_TRACE_DEBUG", "false")):
            print("Error reading configuration from disk, skipping: %s" % e, file=sys.stderr)
    return config


def _apply_configuration_from_disk(debug_logs: bool = False, local_file_override="", managed_file_override=""):
    """
    Sets the configuration from disk as environment variables.
    This is not ideal and we should consider a better mechanism to
    apply this configuration to the tracer.
    Currently here is the order of precedence (higher takes precedence):
    1. Dynamic remote configuration
    2. Runtime configuration (ie fields set manually by customers / from the ddtrace code)
    3. Managed configuration from disk ("fleet stable config")
    4. Environment variables
    5. Local configuration from disk ("local stable config")
    5. Default values
    """
    for entry in get_configuration_from_disk(
        debug_logs=debug_logs, local_file_override=local_file_override, managed_file_override=managed_file_override
    ):
        if entry["source"] == "fleet_stable_config" or (
            entry["source"] == "local_stable_config" and entry["name"] not in list(os.environ.keys())
        ):
            os.environ[entry["name"]] = str(entry["value"])
