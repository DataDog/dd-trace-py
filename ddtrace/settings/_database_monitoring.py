from envier import validators

from ddtrace.settings._core import DDConfig


class DatabaseMonitoringConfig(DDConfig):
    __prefix__ = "dd_dbm"

    propagation_mode = DDConfig.v(
        str,
        "propagation_mode",
        default="disabled",
        help="Valid Injection Modes: disabled, service, and full",
        validator=validators.choice(["disabled", "full", "service"]),
    )


dbm_config = DatabaseMonitoringConfig()
