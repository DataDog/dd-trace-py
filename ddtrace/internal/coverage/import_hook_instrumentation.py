"""Import-event instrumentation for import-only coverage mode.

Import-only mode (`_DD_COVERAGE_IMPORT_ONLY`) answers the question "is tracking imports
enough to know which files each test depends on?" with file-level coverage collected
exclusively from import events: no code object is instrumented, no sys.monitoring events
are armed, no bytecode is rewritten.

The mechanism is a wrapper around `builtins.__import__`. Every executed import statement
calls it — including imports that hit the `sys.modules` cache, because the IMPORT_NAME
opcode always calls `__import__` even when the module is already imported. That is what
makes statement-level tracking sufficient where module-load-level hooks (meta_path
finders, the `import` audit event) are not: they only fire on cache misses.

For each executed import statement, the wrapper records through the collector's hooks:

- every file the import materialized (the module, its parent packages that were loaded,
  and fromlist submodules) as covered files
- the importing file itself, when it is inside the instrumented paths — a file that
  executes an import statement is considered covered, and it is the root that the
  import-time closure expansion attributes module-level dependencies from
- import dependency edges (importing file -> imported names) in the collector's name
  graph, mirroring what the bytecode-injected import hooks record in instrumented modes

`importlib.import_module` is wrapped separately to record dynamic imports and cache
hits. References to it captured before installation bypass the wrapper: first loads
are still seen by the module watchdog, but cache hits and caller edges are invisible.
Code that executes without importing anything, outside the anchored import closure,
is not observable. See poc/itr-import-poc/README.md for the limits of this model.
"""

import builtins
from functools import lru_cache
import importlib
import os
import sys
import typing as t

from ddtrace.internal.coverage.mode import CoverageMode
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# realpath canonicalization cache: on Python < 3.10 module __file__ is not
# symlink-resolved, so paths must be canonicalized once (macOS /var vs /private/var)
# for events, anchors and backfill to share one key space.
_realpath_cache: dict[str, str] = {}


def _realpath(path: str) -> str:
    cached = _realpath_cache.get(path)
    if cached is None:
        cached = _realpath_cache[path] = os.path.realpath(path)
    return cached


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
    base = pkg.split(".")
    if level > 1:
        cut = level - 1
        if cut >= len(base):
            return name
        base = base[: len(base) - cut]
    return ".".join(base + ([name] if name else []))


@lru_cache(maxsize=4096)
def _module_name_prefixes(abs_name: str) -> tuple[str, ...]:
    """Cache only string work; module objects and their paths must be read live."""
    parts = abs_name.split(".")
    return tuple(".".join(parts[:i]) for i in range(1, len(parts) + 1))


def _record(
    collector: CoverageMode,
    should_track: t.Callable[[str], bool],
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
    caller = _realpath(caller)

    # Files the import materialized: the module, any parent packages that were loaded,
    # and fromlist submodules (`from pkg import mod` can pull in both pkg/__init__.py
    # and pkg/mod.py).
    candidate_files = []
    for module_name in _module_name_prefixes(abs_name):
        module = sys.modules.get(module_name)
        file = getattr(module, "__file__", None)
        if file:
            candidate_files.append(_realpath(file))
    if fromlist:
        for sub in fromlist:
            if isinstance(sub, str) and sub:
                module = sys.modules.get(f"{abs_name}.{sub}")
                file = getattr(module, "__file__", None)
                if file:
                    candidate_files.append(_realpath(file))

    for file in candidate_files:
        if should_track(file):
            collector.record_import_file(file)

    if not should_track(caller):
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


def _resolve_import_module_absolute_name(name: str, package: t.Optional[str]) -> t.Optional[str]:
    """Resolve an importlib.import_module name the way importlib does."""
    if not name.startswith("."):
        return name
    if not package:
        return None
    base = package.split(".")
    level = len(name) - len(name.lstrip("."))
    if level > 1:
        cut = level - 1
        if cut >= len(base):
            return None
        base = base[: len(base) - cut]
    rest = name.lstrip(".")
    return ".".join(base + ([rest] if rest else []))


class _ImportHook:
    """One installation, retaining its own delegates after it is deactivated.

    Other tools can retain our wrappers. Clearing shared delegate globals breaks
    those callers on uninstall; replacing them on reinstall creates a cycle.
    """

    def __init__(self, collector: CoverageMode, should_track: t.Callable[[str], bool]) -> None:
        self.collector: t.Optional[CoverageMode] = collector
        self.should_track = should_track
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
                    _record(collector, self.should_track, abs_name, fromlist, globals)
            except Exception:
                # Recording must never break an import.
                log.debug("Import hook recording failed for %r", name, exc_info=True)
        return module

    def import_module(self, name: str, package: t.Optional[str] = None) -> t.Any:
        module = self.original_import_module(name, package)
        collector = self.collector
        if collector is not None:
            try:
                abs_name = _resolve_import_module_absolute_name(name, package)
                if abs_name:
                    _record(collector, self.should_track, abs_name, (), sys._getframe(1).f_globals)
            except Exception:
                # Recording must never break an import.
                log.debug("importlib.import_module hook recording failed for %r", name, exc_info=True)
        return module


def install_import_hook(collector: CoverageMode, should_track: t.Callable[[str], bool]) -> None:
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
    hook.should_track = lambda path: False
    if builtins.__import__ == hook.import_name:
        builtins.__import__ = hook.original_import
    if importlib.import_module == hook.import_module:
        importlib.import_module = hook.original_import_module
    _installed_hook = None
    _realpath_cache.clear()
    _module_name_prefixes.cache_clear()


def import_hook_installed() -> bool:
    return _installed_hook is not None
