from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.settings.symbol_db import config as symdb_config


def bootstrap() -> None:
    if symdb_config._force:
        # Force the upload of symbols, ignoring RCM instructions.
        from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

        SymbolDatabaseUploader.install()
    else:
        # Start the RCM subscriber to determine if and when to upload symbols.
        from ddtrace.internal.symbol_db.remoteconfig import _rc_callback

        remoteconfig_poller.register_callback("LIVE_DEBUGGING_SYMBOL_DB", _rc_callback)
        remoteconfig_poller.enable_product("LIVE_DEBUGGING_SYMBOL_DB")


def restart() -> None:
    from ddtrace.internal.symbol_db.remoteconfig import _rc_callback

    remoteconfig_poller.unregister_callback("LIVE_DEBUGGING_SYMBOL_DB")
    remoteconfig_poller.disable_product("LIVE_DEBUGGING_SYMBOL_DB")
    remoteconfig_poller.register_callback("LIVE_DEBUGGING_SYMBOL_DB", _rc_callback)
    remoteconfig_poller.enable_product("LIVE_DEBUGGING_SYMBOL_DB")
