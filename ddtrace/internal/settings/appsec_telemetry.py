import typing as t

from ddtrace.internal.settings._core import DDConfig


class AppSecTelemetryConfig(DDConfig):
    __prefix__ = "dd"

    SCA_ENABLED = DDConfig.v(t.Optional[bool], "appsec.sca.enabled", default=None)
    SCA_CVE_FILE = DDConfig.v(t.Optional[str], "appsec.sca.cve.file", default=None, private=True)
    ENDPOINT_COLLECTION_ENABLED = DDConfig.v(bool, "api.security.endpoint.collection.enabled", default=True)
    ENDPOINT_COLLECTION_LIMIT = DDConfig.v(int, "api.security.endpoint.collection.message.limit", default=300)


config = AppSecTelemetryConfig()


class AppSecTelemetryProvider:
    """What telemetry reads from the AppSec settings, declared in the ``ddtrace_internal.telemetry_providers`` entry
    point group. The settings are read on each call, so changes at runtime apply.
    """

    def configurations(self) -> tuple[AppSecTelemetryConfig, ...]:
        return (config,)

    def dependency_metadata_enabled(self) -> bool:
        return bool(config.SCA_ENABLED)

    def endpoint_collection_enabled(self) -> bool:
        return bool(config.ENDPOINT_COLLECTION_ENABLED)

    def endpoint_collection_limit(self) -> int:
        return int(config.ENDPOINT_COLLECTION_LIMIT)


provider = AppSecTelemetryProvider()
