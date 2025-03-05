from ddtrace.settings.symbol_db import config as symdb_config


def register():
    if not symdb_config._force and symdb_config.enabled:
        # Start the RCM subscriber to determine if and when to upload symbols.
        # DEV: Lazy-load the adapter to avoid the multiprocessing cost on
        # startup.
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
        from ddtrace.internal.symbol_db.remoteconfig import SymbolDatabaseAdapter

        remoteconfig_poller.register("LIVE_DEBUGGING_SYMBOL_DB", SymbolDatabaseAdapter(), restart_on_fork=True)


def unregister():
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.unregister("LIVE_DEBUGGING_SYMBOL_DB")
