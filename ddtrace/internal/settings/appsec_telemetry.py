import typing as t

from ddtrace.internal.settings._core import DDConfig


class AppSecTelemetryConfig(DDConfig):
    __prefix__ = "dd"

    SCA_ENABLED = DDConfig.v(t.Optional[bool], "appsec.sca.enabled", default=None)
    ENDPOINT_COLLECTION_ENABLED = DDConfig.v(bool, "api.security.endpoint.collection.enabled", default=True)
    ENDPOINT_COLLECTION_LIMIT = DDConfig.v(int, "api.security.endpoint.collection.message.limit", default=300)


config = AppSecTelemetryConfig()
