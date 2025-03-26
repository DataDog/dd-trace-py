import re

from ddtrace.settings._core import DDConfig


class SymbolDatabaseConfig(DDConfig):
    __prefix__ = "dd.symbol_database"

    enabled = DDConfig.v(
        bool,
        "upload_enabled",
        default=True,
        help_type="Boolean",
        help="Whether to upload source code symbols to the Datadog backend",
    )

    includes = DDConfig.v(
        set,
        "includes",
        default=set(),
        help_type="List",
        help="List of modules/packages to include in the symbol uploads",
    )
    _includes_re = DDConfig.d(
        re.Pattern, lambda c: re.compile("(" + "|".join(f"^{p}$|^{p}[.]" for p in c.includes) + ")")
    )

    # ---- Private settings ----

    _force = DDConfig.v(
        bool,
        "force_upload",
        default=False,
        private=True,
        help_type="Boolean",
        help="Whether to force symbol uploads, regardless of RC signals",
    )


config = SymbolDatabaseConfig()
