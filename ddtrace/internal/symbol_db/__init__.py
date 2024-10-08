from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.symbol_db.remoteconfig import SymbolDatabaseAdapter
from ddtrace.settings.symbol_db import config as symdb_config


def bootstrap():
    if symdb_config._force:
        # Force the upload of symbols, ignoring RCM instructions.
        from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

        SymbolDatabaseUploader.install()
    else:
        # Start the RCM subscriber to determine if and when to upload symbols.
        remoteconfig_poller.register("LIVE_DEBUGGING_SYMBOL_DB", SymbolDatabaseAdapter())


def restart():
    remoteconfig_poller.unregister("LIVE_DEBUGGING_SYMBOL_DB")
    remoteconfig_poller.register("LIVE_DEBUGGING_SYMBOL_DB", SymbolDatabaseAdapter())
