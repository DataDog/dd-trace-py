import re

from envier.env import EnvVariable

from ddtrace.internal.settings._core import DDConfig


def _derive_includes_re(config: "SymbolDatabaseConfig") -> re.Pattern[str]:
    return re.compile("(" + "|".join(f"^{p}$|^{p}[.]" for p in config.includes) + ")")


class SymbolDatabaseConfig(DDConfig):
    __prefix__ = "dd.symbol_database"

    enabled = DDConfig.v(
        bool,
        "upload_enabled",
        default=True,
        help_type="Boolean",
        help="Whether to upload source code symbols to the Datadog backend",
    )

    includes: EnvVariable[set[str]] = DDConfig.v(
        set,
        "includes",
        default=set(),
        help_type="List",
        help="List of modules/packages to include in the symbol uploads",
    )
    _includes_re = DDConfig.d(re.Pattern, _derive_includes_re)

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
