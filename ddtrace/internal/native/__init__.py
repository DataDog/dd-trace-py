import os
from typing import Dict
from typing import Tuple

from ._native import DDSketch  # noqa: F401
from ._native import PyConfigurator
from ._native import PyTracerMetadata  # noqa: F401
from ._native import store_metadata  # noqa: F401


def get_configuration_from_disk() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Retrieves the tracer configuration from disk. Calls the PyConfigurator object
    to read the configuration from the disk using the libdatadog shared library
    and returns the corresponding configuration
    See https://github.com/DataDog/libdatadog/blob/06d2b6a19d7ec9f41b3bfd4ddf521585c55298f6/library-config/src/lib.rs
    for more information on how the configuration is read from disk
    """
    debug_logs = os.getenv("DD_TRACE_DEBUG", "false").lower().strip() in ("true", "1")
    configurator = PyConfigurator(debug_logs)

    # Check if the file override is provided via environment variables
    # This is only used for testing purposes
    local_file_override = os.getenv("_DD_SC_LOCAL_FILE_OVERRIDE", "")
    managed_file_override = os.getenv("_DD_SC_MANAGED_FILE_OVERRIDE", "")
    if local_file_override:
        configurator.set_local_file_override(local_file_override)
    if managed_file_override:
        configurator.set_managed_file_override(managed_file_override)

    fleet_config = {}
    local_config = {}
    try:
        for entry in configurator.get_configuration():
            env = entry["name"]
            source = entry["source"]
            if source == "fleet_stable_config":
                fleet_config[env] = entry["value"]
            elif source == "local_stable_config":
                local_config[env] = entry["value"]
            else:
                print(f"Unknown configuration source: {source}, for {env}")
    except Exception as e:
        # No logger at this point, so we rely on good old print
        print(f"Failed to load configuration from disk, skipping: {e}")
    return fleet_config, local_config
