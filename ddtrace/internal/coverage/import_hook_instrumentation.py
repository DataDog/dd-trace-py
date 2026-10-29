"""Wrap import statements and importlib.import_module, including cache hits.

Unlike module-load hooks, these wrappers see repeated imports. They record the
caller, imported files, and dependency names without instrumenting code. References
captured before installation bypass the wrappers; see poc/itr-import-poc/README.md.
"""

import builtins
from functools import lru_cache
import importlib
from importlib.util import resolve_name
import os
import sys
import typing as t

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class ImportRecorder(t.Protocol):
    def record_import_file(self, path: str) -> None: ...
    def record_import_edge(self, path: str, import_name: tuple[str, tuple[str, ...]]) -> None: ...


class _TrackedPaths(dict[str, t.Optional[str]]):
    """One lookup returns a canonical included path, or None for an exclusion.

    On Python < 3.10 module __file__ is not symlink-resolved. Canonicalization
    keeps events, anchors and backfill in the same key space (e.g. /var versus
    /private/var on macOS). Include/exclude paths are fixed for each installation.
    """

    def __init__(self, should_track: t.Callable[[str], bool]) -> None:
        super().__init__()
        self.should_track = should_track

    def __missing__(self, path: str) -> t.Optional[str]:
        resolved = os.path.realpath(path)
        tracked = resolved if self.should_track(resolved) else None
        self[path] = tracked
        return tracked


_installed_hook: t.Optional["_ImportHook"] = None


def _resolve_absolute_name(name: str, glb: t.Optional[t.Mapping[str, t.Any]], level: int) -> str:
    """Resolve a possibly-relative import name to its absolute module name.

    Mirrors the resolution builtins.__import__ performs for level > 0, using
    __package__ from the caller's globals.
    """
    if not level:
        return name
    if glb is None:
        return name
    pkg = glb.get("__package__")
    if not pkg:
        return name
    return resolve_name("." * level + name, pkg)


@lru_cache(maxsize=4096)
def _module_name_prefixes(abs_name: str) -> tuple[str, ...]:
    """Cache only string work; module objects and their paths must be read live."""
    parts = abs_name.split(".")
    return tuple(".".join(parts[:i]) for i in range(1, len(parts) + 1))


def _record(
    collector: ImportRecorder,
    tracked_paths: _TrackedPaths,
    abs_name: str,
    fromlist: t.Any,
    glb: t.Optional[t.Mapping[str, t.Any]],
) -> None:
    # The caller file is normally available as __file__ in the import statement's
    # globals — a dict lookup, much cheaper than the frame inspection fallback.
    caller = glb.get("__file__") if glb is not None else None
    if not caller:
        try:
            caller = sys._getframe(2).f_code.co_filename
        except Exception:
            return
    caller = tracked_paths[caller]

    # A simple import has exactly one candidate. Excluded callers can finish
    # here without prefix expansion or caller/edge bookkeeping, but a framework
    # importing a workspace module must still record that target.
    if caller is None and not fromlist and "." not in abs_name:
        file = getattr(sys.modules.get(abs_name), "__file__", None)
        if file and (tracked := tracked_paths[file]) is not None:
            collector.record_import_file(tracked)
        return

    # Files the import materialized: the module, any parent packages that were loaded,
    # and fromlist submodules (`from pkg import mod` can pull in both pkg/__init__.py
    # and pkg/mod.py).
    for module_name in _module_name_prefixes(abs_name):
        module = sys.modules.get(module_name)
        file = getattr(module, "__file__", None)
        if file and (tracked := tracked_paths[file]) is not None:
            collector.record_import_file(tracked)
    if fromlist:
        for sub in fromlist:
            if isinstance(sub, str) and sub:
                module = sys.modules.get(f"{abs_name}.{sub}")
                file = getattr(module, "__file__", None)
                if file and (tracked := tracked_paths[file]) is not None:
                    collector.record_import_file(tracked)

    # An excluded caller may import workspace code, including cached modules.
    # Read target files live before returning, but skip all caller/edge recording.
    if caller is None:
        return

    # The importing file executed an import statement: it is covered.
    collector.record_import_file(caller)

    # These names are already absolute. Passing the caller's package would let
    # the shared resolver select an unrelated package-local module with the same
    # name and would walk the wrong parent packages.
    names = [abs_name]
    if fromlist:
        names.extend(f"{abs_name}.{sub}" for sub in fromlist if isinstance(sub, str) and sub)
    collector.record_import_edge(caller, ("", tuple(names)))


class _ImportHook:
    """One installation, retaining its own delegates after it is deactivated.

    Other tools can retain our wrappers. Clearing shared delegate globals breaks
    those callers on uninstall; replacing them on reinstall creates a cycle.
    """

    def __init__(self, collector: ImportRecorder, should_track: t.Callable[[str], bool]) -> None:
        self.collector: t.Optional[ImportRecorder] = collector
        self.tracked_paths = _TrackedPaths(should_track)
        self.original_import = builtins.__import__
        self.original_import_module = importlib.import_module

    def import_name(
        self,
        name: str,
        globals: t.Optional[t.Mapping[str, t.Any]] = None,  # noqa: A002
        locals: t.Optional[t.Mapping[str, t.Any]] = None,  # noqa: A002
        fromlist: t.Any = (),
        level: int = 0,
    ) -> t.Any:
        module = self.original_import(name, globals, locals, fromlist, level)
        # Failed imports raise above and record nothing, matching injected hooks.
        collector = self.collector
        if collector is not None:
            try:
                abs_name = _resolve_absolute_name(name, globals, level)
                if abs_name:
                    _record(collector, self.tracked_paths, abs_name, fromlist, globals)
            except Exception:
                # Recording must never break an import.
                log.debug("Import hook recording failed for %r", name, exc_info=True)
        return module

    def import_module(self, name: str, package: t.Optional[str] = None) -> t.Any:
        module = self.original_import_module(name, package)
        collector = self.collector
        if collector is not None:
            try:
                abs_name = resolve_name(name, package)
                if abs_name:
                    _record(collector, self.tracked_paths, abs_name, (), sys._getframe(1).f_globals)
            except Exception:
                # Recording must never break an import.
                log.debug("importlib.import_module hook recording failed for %r", name, exc_info=True)
        return module


def install_import_hook(collector: ImportRecorder, should_track: t.Callable[[str], bool]) -> None:
    """Wrap import statements and importlib.import_module, including cache hits."""
    global _installed_hook
    if _installed_hook is not None:
        return
    hook = _installed_hook = _ImportHook(collector, should_track)
    builtins.__import__ = hook.import_name
    importlib.import_module = hook.import_module


def uninstall_import_hook() -> None:
    """Deactivate retained wrappers and restore hooks we still own."""
    global _installed_hook
    hook = _installed_hook
    if hook is None:
        return
    hook.collector = None
    # Release the bound should_track method too: it retains the collector.
    hook.tracked_paths.should_track = lambda path: False
    hook.tracked_paths.clear()
    if builtins.__import__ == hook.import_name:
        builtins.__import__ = hook.original_import
    if importlib.import_module == hook.import_module:
        importlib.import_module = hook.original_import_module
    _installed_hook = None
    _module_name_prefixes.cache_clear()


def import_hook_installed() -> bool:
    return _installed_hook is not None
