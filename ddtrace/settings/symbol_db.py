from envier import En


class SymbolDatabaseConfig(En):
    enabled = En.v(
        bool,
        "dd.symbol_database.upload_enabled",
        default=False,
        help_type="Boolean",
        help="Whether to upload source code symbols to the Datadog backend",
    )

    includes = En.v(
        set,
        "dd.symbol_database.includes",
        default=set(),
        help_type="List",
        help="List of modules/packages to include in the symbol uploads",
    )

    _force = En.v(
        bool,
        "_dd.symbol_database.force_upload",
        default=False,
        help_type="Boolean",
        help="Whether to force symbol uploads, regardless of RC signals",
    )


config = SymbolDatabaseConfig()
