import re

from envier import En

from ddtrace.settings._core import report_telemetry as _report_telemetry


class SymbolDatabaseConfig(En):
    __prefix__ = "dd.symbol_database"

    enabled = En.v(
        bool,
        "upload_enabled",
        default=True,
        help_type="Boolean",
        help="Whether to upload source code symbols to the Datadog backend",
    )

    includes = En.v(
        set,
        "includes",
        default=set(),
        help_type="List",
        help="List of modules/packages to include in the symbol uploads",
    )
    _includes_re = En.d(re.Pattern, lambda c: re.compile("(" + "|".join(f"^{p}$|^{p}[.]" for p in c.includes) + ")"))

    # ---- Private settings ----

    _force = En.v(
        bool,
        "force_upload",
        default=False,
        private=True,
        help_type="Boolean",
        help="Whether to force symbol uploads, regardless of RC signals",
    )


config = SymbolDatabaseConfig()
_report_telemetry(config)
