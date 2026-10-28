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

Known blind spots, by design: modules imported through `importlib` machinery
(`importlib.import_module` bypasses `builtins.__import__`) never record edges; their
own files are still recorded at transform/after_import time by the collector. Files whose
code executes during a test without any import statement executing from a tracked file
and outside the import-time closure are not attributed (measured in practice to be test
harness files, e.g. conftest).
"""

import builtins
import sys
import typing as t

from ddtrace.internal.logger import get_logger


ImportName = tuple[str, tuple[str, ...]]

log = get_logger(__name__)

_original_import: t.Optional[t.Callable[..., t.Any]] = None
_collector: t.Optional[t.Any] = None
_should_track: t.Optional[t.Callable[[str], bool]] = None


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


def _record(
    collector: t.Any,
    should_track: t.Callable[[str], bool],
    abs_name: str,
    fromlist: t.Any,
    glb: t.Optional[t.Mapping[str, t.Any]],
) -> None:
    # _record is called from _ddtrace_import_hook, so the import statement's frame is two
    # levels up.
    caller = sys._getframe(2).f_code.co_filename

    # Files the import materialized: the module, any parent packages that were loaded,
    # and fromlist submodules (`from pkg import mod` can pull in both pkg/__init__.py
    # and pkg/mod.py).
    candidate_files = []
    parts = abs_name.split(".")
    for i in range(1, len(parts) + 1):
        module = sys.modules.get(".".join(parts[:i]))
        file = getattr(module, "__file__", None)
        if file:
            candidate_files.append(file)
    if fromlist:
        for sub in fromlist:
            if isinstance(sub, str) and sub:
                module = sys.modules.get(f"{abs_name}.{sub}")
                file = getattr(module, "__file__", None)
                if file:
                    candidate_files.append(file)

    for file in candidate_files:
        if should_track(file):
            collector.hook_import_file(file)

    if not should_track(caller):
        return

    # The importing file executed an import statement: it is covered.
    collector.hook_import_file(caller)

    # Dependency edge for the import-time closure, in the same (package, names) shape the
    # bytecode-injected import hooks record. The collector's resolution logic tries
    # "<package>.<name>" first and falls back to the bare name, so absolute names work
    # regardless of the caller's package.
    caller_package = ""
    if glb is not None:
        caller_package = glb.get("__package__") or ""
    names = [abs_name]
    if fromlist:
        names.extend(f"{abs_name}.{sub}" for sub in fromlist if isinstance(sub, str) and sub)
    collector.hook_import(caller, (caller_package, tuple(names)))


def _ddtrace_import_hook(
    name: str,
    globals: t.Optional[t.Mapping[str, t.Any]] = None,  # noqa: A002
    locals: t.Optional[t.Mapping[str, t.Any]] = None,  # noqa: A002
    fromlist: t.Any = (),
    level: int = 0,
) -> t.Any:
    original_import = t.cast(t.Callable[..., t.Any], _original_import)
    module = original_import(name, globals, locals, fromlist, level)
    # Failed imports raise above and record nothing, matching the injected import hooks
    # (which sit after the import opcode).
    try:
        abs_name = _resolve_absolute_name(name, globals, level)
        if abs_name and _collector is not None and _should_track is not None:
            _record(_collector, _should_track, abs_name, fromlist, globals)
    except Exception:
        # Recording must never break an import.
        log.debug("Import hook recording failed for %r", name, exc_info=True)
    return module


def install_import_hook(collector: t.Any, should_track: t.Callable[[str], bool]) -> None:
    """Wrap builtins.__import__ so import statements record coverage.

    collector must provide hook_import_file(path) and hook_import(path, import_name).
    should_track(path) decides whether a file is inside the instrumented paths.
    """
    global _original_import, _collector, _should_track
    if _collector is not None:
        return
    _original_import = builtins.__import__
    _collector = collector
    _should_track = should_track
    builtins.__import__ = _ddtrace_import_hook


def uninstall_import_hook() -> None:
    """Restore builtins.__import__, unless someone wrapped it on top of us."""
    global _original_import, _collector, _should_track
    if _collector is None:
        return
    if builtins.__import__ is _ddtrace_import_hook:
        builtins.__import__ = t.cast(t.Any, _original_import)
    _original_import = None
    _collector = None
    _should_track = None


def import_hook_installed() -> bool:
    return _collector is not None
