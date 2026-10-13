import typing as t

from ddtrace.internal.settings._core import DDConfig


class SCAConfig(DDConfig):
    __prefix__ = "dd"

    SCA_ENABLED = DDConfig.v(t.Optional[bool], "appsec.sca.enabled", default=None)


config = SCAConfig()
