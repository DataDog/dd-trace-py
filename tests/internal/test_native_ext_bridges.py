"""ddtrace.internal.native's compiled extensions are the ddtrace-native modules, loaded once."""

from pathlib import Path
import sys

import _ddtrace_native._msgpack
import _ddtrace_native._native
import _ddtrace_native._threads

import ddtrace.internal.native as native
import ddtrace.internal.native._msgpack  # noqa: F401 -- installs the bridge
import ddtrace.internal.native._threads  # noqa: F401
from ddtrace.internal.threads import PeriodicThread


def test_bridged_native_is_the_package_native():
    assert sys.modules["ddtrace.internal.native._native"] is _ddtrace_native._native
    assert native._native is _ddtrace_native._native
    assert native.seed is _ddtrace_native._native.seed


def test_bridged_extensions_are_the_package_extensions():
    assert sys.modules["ddtrace.internal.native._threads"] is _ddtrace_native._threads
    assert sys.modules["ddtrace.internal.native._msgpack"] is _ddtrace_native._msgpack
    assert native._threads.periodic_threads is _ddtrace_native._threads.periodic_threads
    assert native._threads.PERIODIC_STOP is _ddtrace_native._threads.PERIODIC_STOP
    assert issubclass(PeriodicThread, _ddtrace_native._threads.PeriodicThread)
    assert native._msgpack.packb is _ddtrace_native._msgpack.packb


def test_no_stale_extension_shadows_a_bridge():
    # An extension module wins over a .py file with the same name, so a leftover build would replace the bridge.
    package_dir = Path(native.__file__).parent
    assert not [
        p.name
        for p in package_dir.iterdir()
        if p.name.startswith(("_threads.", "_msgpack.", "_native.")) and p.suffix not in (".py", ".pyi")
    ]
