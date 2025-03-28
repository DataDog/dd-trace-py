from ddtrace.settings.symbol_db import config


requires = ["remote-configuration"]


def post_preload():
    pass


def start():
    if config._force:
        # Force the upload of symbols, ignoring RCM instructions.
        from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

        SymbolDatabaseUploader.install()


def restart(join=False):
    pass


def stop(join=False):
    # Controlled via RC
    pass


def at_exit(join=False):
    stop(join=join)
