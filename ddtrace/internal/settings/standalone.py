"""Standalone mode, a.k.a. APM opt-out.

Standalone is not owned by any single product: it is on whenever one of the security or AI
products is enabled while APM tracing is turned off. This module owns DD_APM_TRACING_ENABLED and
aggregates the per-product flags, so no product config has to know about the others.
"""

from ddtrace.internal.constants import APM_TRACING_ENV
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry import report_configuration


class StandaloneConfig(DDConfig):
    # Mutated at runtime by the public Tracer.configure(apm_tracing_disabled=...) API.
    apm_tracing_enabled = DDConfig.var(bool, APM_TRACING_ENV, default=True)

    # for tests purposes
    _standalone_config_keys = ["apm_tracing_enabled"]

    @property
    def apm_opt_out(self) -> bool:
        """Whether a product runs standalone, i.e. enabled while APM tracing is off."""
        return (
            asm_config._asm_enabled
            or asm_config._iast_enabled
            or appsec_telemetry_config.SCA_ENABLED is True
            or aiguard_config._ai_guard_enabled
        ) and not self.apm_tracing_enabled

    def reset(self) -> None:
        """For testing purposes, reset the configuration to its default values given current environment variables."""
        self.__init__()  # type: ignore[misc]


standalone_config = StandaloneConfig()
# No product plugin owns this config, so report it here or DD_APM_TRACING_ENABLED would drop
# out of configuration telemetry (it used to ride along with the appsec product's ASMConfig).
report_configuration(standalone_config)
