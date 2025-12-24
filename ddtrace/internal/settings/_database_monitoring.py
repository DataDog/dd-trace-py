from envier import validators

from ddtrace.internal.settings._core import DDConfig


class DatabaseMonitoringConfig(DDConfig):
    __prefix__ = "dd_dbm"

    propagation_mode = DDConfig.v(
        str,
        "propagation_mode",
        default="disabled",
        help="Valid Injection Modes: disabled, service, and full",
        validator=validators.choice(["disabled", "full", "service"]),
    )

    inject_sql_basehash = DDConfig.v(
        bool,
        "inject_sql_basehash",
        help="Inject Base Hash in SQL Comments",
        default=False,
    )


dbm_config = DatabaseMonitoringConfig()
