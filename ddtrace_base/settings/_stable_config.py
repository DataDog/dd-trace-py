"""Stable (fleet/local) configuration discovery.

The existence check (fast path) is pure; the native ``PyConfigurator`` is imported only when a
stable-configuration file is actually present on disk.
"""

import os
import sys
from typing import Optional

from ddtrace_base.settings.env import dd_environ as env


# Default stable-configuration file paths, mirroring the target constants defined
# by libdatadog's Configurator (see src/native/library_config.rs). They are only
# used for the existence check below: when neither file is present there is
# nothing to parse, so no PyConfigurator is constructed and no file is opened.
# NOTE: keep these in sync with libdatadog -- the `stable_configuration_paths`
# function exposed by the native extension asserts they stay aligned (see
# `test_stable_config_paths_match_native` in tests/internal/test_native.py).
if sys.platform == "win32":
    _FLEET_STABLE_CONFIGURATION_PATH = (
        "C:\\ProgramData\\Datadog\\managed\\datadog-agent\\stable\\application_monitoring.yaml"
    )
    _LOCAL_STABLE_CONFIGURATION_PATH = "C:\\ProgramData\\Datadog\\application_monitoring.yaml"
elif sys.platform == "darwin":
    _FLEET_STABLE_CONFIGURATION_PATH = "/opt/datadog-agent/etc/stable/application_monitoring.yaml"
    _LOCAL_STABLE_CONFIGURATION_PATH = "/opt/datadog-agent/etc/application_monitoring.yaml"
else:
    _FLEET_STABLE_CONFIGURATION_PATH = "/etc/datadog-agent/managed/datadog-agent/stable/application_monitoring.yaml"
    _LOCAL_STABLE_CONFIGURATION_PATH = "/etc/datadog-agent/application_monitoring.yaml"


def _stable_config_paths() -> tuple[str, str]:
    """Return the ``(fleet, local)`` stable-configuration file paths to check.

    Honors the test-only ``_DD_SC_*_FILE_OVERRIDE`` environment variables also
    consumed by ``get_configuration_from_disk()`` so that the existence check and
    the native reader always look at the same files.
    """
    fleet = env.get("_DD_SC_MANAGED_FILE_OVERRIDE") or _FLEET_STABLE_CONFIGURATION_PATH
    local = env.get("_DD_SC_LOCAL_FILE_OVERRIDE") or _LOCAL_STABLE_CONFIGURATION_PATH
    return fleet, local


def get_configuration_from_disk() -> tuple[dict[str, str], dict[str, str], dict[str, Optional[str]]]:
    """
    Retrieves the tracer configuration from disk. Calls the PyConfigurator object
    to read the configuration from the disk using the libdatadog shared library
    and returns the corresponding configuration
    See https://github.com/DataDog/libdatadog/blob/06d2b6a19d7ec9f41b3bfd4ddf521585c55298f6/library-config/src/lib.rs
    for more information on how the configuration is read from disk

    Fast path: when no stable-configuration file is present on disk there is
    nothing to read, so no PyConfigurator is constructed and no file is opened.
    (Importing this module is also cheap: the compiled extension is only loaded
    on demand, see ``__getattr__`` above.)
    """
    fleet_path, local_path = _stable_config_paths()
    if not (os.path.exists(fleet_path) or os.path.exists(local_path)):
        return {}, {}, {}

    # The native reader is only needed when a file exists. It ships with ddtrace; a standalone
    # consumer of the base layer (ddtrace not installed) cannot parse stable configuration.
    try:
        from ddtrace.internal.native._native import PyConfigurator
    except ImportError as e:
        print(f"Stable configuration file present but no reader is available, skipping: {e}")
        return {}, {}, {}

    debug_logs = env.get("DD_TRACE_DEBUG", "false").lower().strip() in ("true", "1")
    configurator = PyConfigurator(debug_logs)

    # Check if the file override is provided via environment variables
    # This is only used for testing purposes
    local_file_override = env.get("_DD_SC_LOCAL_FILE_OVERRIDE", "")
    managed_file_override = env.get("_DD_SC_MANAGED_FILE_OVERRIDE", "")
    if local_file_override:
        configurator.set_local_file_override(local_file_override)
    if managed_file_override:
        configurator.set_managed_file_override(managed_file_override)

    fleet_config = {}
    fleet_config_ids = {}
    local_config = {}
    try:
        for entry in configurator.get_configuration():
            var_name = entry["name"]
            source = entry["source"]
            if source == "fleet_stable_config":
                fleet_config[var_name] = entry["value"]
                fleet_config_ids[var_name] = entry.get("config_id")
            elif source == "local_stable_config":
                local_config[var_name] = entry["value"]
            else:
                print(f"Unknown configuration source: {source}, for {var_name}")
    except Exception as e:
        # No logger at this point, so we rely on good old print
        print(f"Failed to load configuration from disk, skipping: {e}")
    return fleet_config, local_config, fleet_config_ids
