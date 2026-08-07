from ddtrace.internal.native import RemoteConfigProduct
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

        remoteconfig_poller.register_callback(RemoteConfigProduct.LiveDebuggerSymbolDb, _rc_callback)
        remoteconfig_poller.enable_product(RemoteConfigProduct.LiveDebuggerSymbolDb)


def restart() -> None:
    from ddtrace.internal.symbol_db.remoteconfig import _rc_callback

    remoteconfig_poller.unregister_callback(RemoteConfigProduct.LiveDebuggerSymbolDb)
    remoteconfig_poller.disable_product(RemoteConfigProduct.LiveDebuggerSymbolDb)
    remoteconfig_poller.register_callback(RemoteConfigProduct.LiveDebuggerSymbolDb, _rc_callback)
    remoteconfig_poller.enable_product(RemoteConfigProduct.LiveDebuggerSymbolDb)
