from envier import En


class SymbolDatabaseConfig(En):
    __prefix__ = "dd.symbol_database"

    enabled = En.v(
        bool,
        "upload_enabled",
        default=False,
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
