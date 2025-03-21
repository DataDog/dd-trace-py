import sys
import typing as t

from ddtrace.settings._core import DDConfig
from ddtrace.settings._inferred_base_service import detect_service


class TelemetryConfig(DDConfig):
    __prefix__ = "dd"

    API_KEY = DDConfig.v(t.Optional[str], "api_key", default=None)
    SITE = DDConfig.v(str, "site", default="datadoghq.com")
    ENV = DDConfig.v(str, "env", default="")
    SERVICE = DDConfig.v(str, "service", default=detect_service(sys.argv) or "unnamed-python-service")
    VERSION = DDConfig.v(str, "version", default="")
    AGENTLESS_MODE = DDConfig.v(bool, "civisibility.agentless.enabled", default=False)
    DEBUG = DDConfig.v(bool, "trace.debug", default=False)
    HEARTBEAT_INTERVAL = DDConfig.v(float, "telemetry.heartbeat_interval", default=60.0)
    TELEMETRY_ENABLED = DDConfig.v(bool, "instrumentation_telemetry.enabled", default=True)
    DEPENDENCY_COLLECTION = DDConfig.v(bool, "telemetry.dependency_collection.enabled", default=True)
    INSTALL_ID = DDConfig.v(t.Optional[str], "instrumentation.install_id", default=None)
    INSTALL_TYPE = DDConfig.v(t.Optional[str], "instrumentation.install_type", default=None)
    INSTALL_TIME = DDConfig.v(t.Optional[str], "instrumentation.install_time", default=None)
    FORCE_START = DDConfig.v(bool, "instrumentation_telemetry.tests.force_app_started", default=False, private=True)
    LOG_COLLECTION_ENABLED = DDConfig.v(bool, "telemetry.log_collection.enabled", default=True)


config = TelemetryConfig()
