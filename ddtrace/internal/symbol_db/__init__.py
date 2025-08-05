from functools import partial

from ddtrace.internal import core
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.symbol_db.remoteconfig import SymbolDatabaseAdapter
from ddtrace.settings.symbol_db import config as symdb_config


def bootstrap():
    if symdb_config._force:
        # Force the upload of symbols, ignoring RCM instructions.
        from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

        SymbolDatabaseUploader.install(shallow=False)
    else:
        # Start the RCM subscriber to determine if and when to upload symbols.
        remoteconfig_poller.register("LIVE_DEBUGGING_SYMBOL_DB", SymbolDatabaseAdapter())

    @partial(core.on, "dynamic-instrumentation.enabled")
    def _():
        if not symdb_config.enabled:
            return

        from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

        # If dynamic instrumentation is enabled, we need to restart Symbol DB
        # to ensure that we recursively collect all symbols from modules.
        # To avoid tracking the status of all loaded modules, we keep Symbol DB
        # installed, even if DI is disabled.
        if SymbolDatabaseUploader.is_installed() and SymbolDatabaseUploader.shallow:
            SymbolDatabaseUploader.uninstall()
            SymbolDatabaseUploader.install(shallow=False)


def restart():
    remoteconfig_poller.unregister("LIVE_DEBUGGING_SYMBOL_DB")
    remoteconfig_poller.register("LIVE_DEBUGGING_SYMBOL_DB", SymbolDatabaseAdapter())
