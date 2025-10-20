# This file is part of "echion" which is released under MIT.
#
# Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

import atexit
import os
import sys
from types import ModuleType

import echion.core as ec
from echion.module import ModuleWatchdog


# We cannot unregister the fork hook, so we use this flag instead
do_on_fork = True


def restart_on_fork():
    global do_on_fork
    if not do_on_fork:
        return

    # Restart sampling after fork
    ec.stop()
    ec.init()
    start()


def start():
    global do_on_fork

    # Set the configuration
    ec.set_interval(int(os.getenv("ECHION_INTERVAL", 1000)))
    ec.set_cpu(bool(int(os.getenv("ECHION_CPU", 0))))
    ec.set_memory(bool(int(os.getenv("ECHION_MEMORY", 0))))
    ec.set_native(bool(int(os.getenv("ECHION_NATIVE", 0))))
    ec.set_where(bool(int(os.getenv("ECHION_WHERE", 0) or 0)))

    # Monkey-patch the standard library on import
    try:
        ModuleWatchdog.install()
        atexit.register(ModuleWatchdog.uninstall)
    except RuntimeError:
        # If ModuleWatchdog is installed we have already registered the import
        # hooks.
        pass
    else:
        for module in ("asyncio", "threading", "gevent"):

            @ModuleWatchdog.after_module_imported(module)
            def _(module: ModuleType) -> None:
                echion_module = f"echion.monkey.{module.__name__}"
                __import__(echion_module)
                sys.modules[echion_module].patch()
                sys.modules[echion_module].track()

    do_on_fork = True
    os.register_at_fork(after_in_child=restart_on_fork)
    atexit.register(stop)

    if int(os.getenv("ECHION_STEALTH", 0)):
        ec.start_async()
    else:
        from threading import Thread

        Thread(target=ec.start, name="echion.core.sampler", daemon=True).start()


def stop():
    global do_on_fork

    ec.stop()

    for module in ("asyncio", "threading"):
        echion_module = f"echion.monkey.{module}"
        try:
            sys.modules[echion_module].unpatch()
        except KeyError:
            pass

    ModuleWatchdog.uninstall()

    atexit.unregister(stop)
    atexit.unregister(ModuleWatchdog.uninstall)

    do_on_fork = False
