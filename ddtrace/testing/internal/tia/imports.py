# Adapted from pytest-filemon (Apache-2.0).
"""Import-only dependency collection (no code instrumentation).

Wraps `builtins.__import__` and `importlib.import_module` and turns
every executed import statement into a **file** dependency.  The wraps see
*cached* imports too -- an import that hits `sys.modules` fires no code
event anywhere, but the statement still runs through
`builtins.__import__`, which is what makes whole-file dependencies
complete without any code instrumentation: to call into a module, a test
must execute an import statement for it, and every such statement is
recorded.  No `sys.monitoring`, no bytecode analysis; the recorder works
on any Python that runs pytest.

A dependency is a whole repository file: any change to it re-runs its
dependents.  (Mortadelo, the block-level sibling, needs its import scan,
fixture closure and `lru_cache` wraps precisely because a skeleton does
not cover a function body; at file granularity the plain import edges
subsume all three.)

Buckets (driven by the plugin through `phase` / `current_nodeid`):

* `import`  -- everything imported before collection starts.  Shared by
  every recorded test.  Modules already imported at arming time are
  claimed here, and their full ASTs are backfilled: every import
  statement in them -- including dead branches -- whose target is
  present in `sys.modules` becomes a dependency.  Backfill deliberately
  over-approximates what ran before the recorder existed; it does not
  reconstruct execution history.
* `collect` -- recorded as edges `(importing file, imported file)`:
  a test module's own imports belong to its tests, a test module
  imported by pytest's machinery belongs to its own tests, and anything
  else is shared by every test (the plugin splits the edges at finish).
* `test`    -- attributed to the running test's node id.

Subprocess spawns are detected by wrapping the process-creation entry
points: coverage cannot follow a child, so a spawning test keeps no
dependencies and re-runs whenever anything changed.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
import typing as t

from ddtrace.testing.internal.tia.paths import to_repo_rel


# (module, attribute) pairs that create a process or replace the image.
# `Popen.__init__` is the choke point of `subprocess`; the others cover
# direct os-level use and `multiprocessing`.  Names missing on a
# platform are skipped.
_SPAWN_TARGETS = (
    ("subprocess", "Popen.__init__"),
    ("subprocess", "_fork_exec"),
    ("_posixsubprocess", "fork_exec"),
    ("os", "system"),
    ("os", "fork"),
    ("os", "forkpty"),
    ("os", "posix_spawn"),
    ("os", "posix_spawnp"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "execvpe"),
)


def _caller_package(globals_: t.Any) -> str | None:
    """The package an import statement's relative level resolves against.

    The same fallback chain CPython's import machinery uses:
    `__package__`, then the spec's parent, then `__name__` (a module's
    parent package, or itself when it is a package).
    """
    if not isinstance(globals_, dict) or not globals_:
        return None
    package = globals_.get("__package__")
    if package is None:
        spec = globals_.get("__spec__")
        package = getattr(spec, "parent", None) if spec is not None else None
    if package is None:
        name = globals_.get("__name__")
        if isinstance(name, str) and name:
            package = name if globals_.get("__path__") is not None else name.rpartition(".")[0]
    return package if isinstance(package, str) and package else None


class ImportMonitor:
    """Records per-test import dependencies, bucketed by execution phase."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        # (owner, name, original, wrapper) of every installed wrap.
        self._undo: list[tuple[t.Any, str, t.Any, t.Any]] = []
        # Per-instance file-resolution cache (absolute paths only).
        self._rel_cache: dict[str, str | None] = {}
        # Buckets.
        self._phase = "import"  # import | collect | test | done
        self._nodeid: str | None = None
        self.import_deps: set[str] = set()
        self.edges: dict[str, set[str]] = {}
        # (importing repo file or None, imported repo file) of the collect
        # phase; split into per-test-module vs. shared at finish.
        self.collect_deps: set[tuple[str | None, str]] = set()
        self._test_deps: dict[str, set[str]] = {}
        self._trash: set[str] = set()
        self._sink: set[str] = self.import_deps
        # Subprocess boundary: tests that spawned a process during their
        # run, plus a shared mark for collect-phase spawns.
        self.spawn_tests: set[str] = set()
        self.spawn_shared = False
        self._active = False
        self.valid = True

    # -- phase / attribution target ------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @phase.setter
    def phase(self, value: str) -> None:
        self._phase = value
        self._update_sink()

    @property
    def current_nodeid(self) -> str | None:
        return self._nodeid

    @current_nodeid.setter
    def current_nodeid(self, value: str | None) -> None:
        self._nodeid = value
        self._update_sink()

    @property
    def test_deps(self) -> dict[str, set[str]]:
        return self._test_deps

    def _update_sink(self) -> None:
        if self._phase == "test" and self._nodeid is not None:
            self._sink = self._test_deps.setdefault(self._nodeid, set())
        elif self._phase in ("import", "fixture"):
            self._sink = self.import_deps
        else:
            self._sink = self._trash

    # -- lifecycle ------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    def activate(self) -> bool:
        if self._active:
            return True
        original = builtins.__import__
        if getattr(original, "_filemon_tracking", False):  # pragma: no cover
            return False  # another ImportMonitor owns the hooks
        builtins.__import__ = self._wrapped_import(original)
        self._undo.append((builtins, "__import__", original, builtins.__import__))
        orig_import_module = importlib.import_module
        if not getattr(orig_import_module, "_filemon_tracking", False):
            importlib.import_module = self._wrapped_import_module(orig_import_module)
            self._undo.append((importlib, "import_module", orig_import_module, importlib.import_module))
        self._install_spawn_wraps()
        self._active = True
        self._backfill()
        return True

    def deactivate(self) -> None:
        self._active = False
        self._phase = "done"
        while self._undo:
            owner, name, orig, wrapper = self._undo.pop()
            try:
                if getattr(owner, name, None) is wrapper:
                    setattr(owner, name, orig)
            except (AttributeError, TypeError):  # pragma: no cover
                pass

    # -- wraps ----------------------------------------------------------

    def _wrapped_import(self, orig: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
        # Preserve the builtin's keyword argument names for callers using them explicitly.
        def import_wrapper(
            name: str,
            globals: t.Any = None,  # noqa: A002
            locals: t.Any = None,  # noqa: A002
            fromlist: t.Any = (),
            level: int = 0,
        ) -> t.Any:
            result = orig(name, globals, locals, fromlist, level)
            if self._active:
                try:
                    self._track_import(name, globals, fromlist, level)
                except Exception:  # never break the import
                    self.valid = False
            return result

        setattr(import_wrapper, "_filemon_tracking", True)
        return import_wrapper

    def _wrapped_import_module(self, orig: t.Callable[..., ModuleType]) -> t.Callable[..., ModuleType]:
        def import_module(name: str, package: t.Optional[str] = None) -> ModuleType:
            result = orig(name, package)
            if self._active:
                try:
                    self._track_import_module(name, package, sys._getframe(1).f_globals.get("__file__"))
                except Exception:  # never break the import
                    self.valid = False
            return result

        setattr(import_module, "_filemon_tracking", True)
        return import_module

    def _install_spawn_wraps(self) -> None:
        for modname, attr in _SPAWN_TARGETS:
            try:
                module = __import__(modname)
            except ImportError:  # pragma: no cover - platform specific
                continue
            owner: t.Any = module
            leaf = attr
            if "." in attr:  # "Popen.__init__"
                cls, _, leaf = attr.partition(".")
                owner = getattr(module, cls, None)
                if owner is None:  # pragma: no cover
                    continue
            orig = getattr(owner, leaf, None)
            if orig is None or getattr(orig, "_filemon_tracking", False):
                continue

            def wrapper(*a: t.Any, __orig: t.Any = orig, **k: t.Any) -> t.Any:
                self._note_spawn()
                return __orig(*a, **k)

            setattr(wrapper, "_filemon_tracking", True)
            try:
                setattr(owner, leaf, wrapper)
            except (AttributeError, TypeError):  # pragma: no cover
                continue
            self._undo.append((owner, leaf, orig, wrapper))

    def _note_spawn(self) -> None:
        """A process is being created: attribute it to the running test.

        * test phase: coverage cannot follow the child, so collection for
          this test stops and the recorder never gives it dependencies --
          it re-runs whenever anything changed.
        * collect phase: the culprit is unknown (module import) -> shared
          mark.
        * import phase: pytest's own startup -- and our own git
          subprocesses -- must not mark every test a spawner.  Done
          phase: recording is over.
        """
        if not self._active:
            return
        try:
            if self._phase == "test" and self._nodeid is not None:
                self.spawn_tests.add(self._nodeid)
                self._sink = self._trash  # collection stops for this test
            elif self._phase in ("collect", "fixture"):
                self.spawn_shared = True
        except Exception:  # pragma: no cover - never break the call we wrap
            self.valid = False

    # -- tracking -------------------------------------------------------

    def _track_import(self, name: str, globals_: t.Any, fromlist: t.Any, level: int) -> None:
        """Record the repo files an executed `import` statement resolved to."""
        caller_file = (globals_ or {}).get("__file__") if isinstance(globals_, dict) else None
        if level:
            package = _caller_package(globals_)
            if package is None:
                return  # the import itself will raise; nothing to record
            parts = package.split(".")
            if level - 1 > len(parts):
                return
            if level > 1:
                parts = parts[: len(parts) - (level - 1)]
            absolute = ".".join([*parts, name]) if name else ".".join(parts)
        else:
            absolute = name
        if not absolute:
            return
        self._record_dotted(absolute, caller_file)
        if fromlist:
            for item in fromlist:
                if isinstance(item, str):
                    self._record_dotted(f"{absolute}.{item}", caller_file)

    def _track_import_module(
        self, name: str, package: t.Optional[str] = None, caller_file: t.Optional[str] = None
    ) -> None:
        """`import_module` produces no import bytecode in the caller: the
        wrap resolves the returned module (cached or not) the same way.
        """
        dotted = importlib.util.resolve_name(name, package) if isinstance(name, str) else name
        self._record_dotted(dotted, caller_file)

    def _record_dotted(self, dotted: str, caller_file: t.Optional[str] = None) -> None:
        """Record *dotted* and every parent package present in sys.modules
        (importing `a.b.c` executes `a` and `a.b` too).
        """
        module = sys.modules.get(dotted)
        if module is not None:
            self._record_file(getattr(module, "__file__", None), caller_file)
        while "." in dotted:
            dotted = dotted.rpartition(".")[0]
            module = sys.modules.get(dotted)
            if module is not None:
                self._record_file(getattr(module, "__file__", None), caller_file)

    def _record_file(self, filename: t.Any, caller_file: t.Optional[str] = None) -> None:
        if not filename or not isinstance(filename, str):
            return
        rel = self.repo_rel(filename)
        if rel is None:
            return
        caller = self.repo_rel(caller_file) if caller_file else None
        if caller is not None:
            self.edges.setdefault(caller, set()).add(rel)
        if self._phase == "collect":
            self.collect_deps.add((caller, rel))
        elif self._phase != "done":
            self._sink.add(rel)

    # -- backfill -------------------------------------------------------

    def _backfill(self) -> None:
        """Claim the modules imported before arming, plus their ASTs.

        The AST scan over-approximates: a conditional import inside a
        dead branch becomes a dependency when its target is (already) in
        `sys.modules` -- loaded by someone else.  Ordinary post-arm
        events record only imports that actually executed.
        """
        for name, module in list(sys.modules.items()):
            if not name or module is None:
                continue
            file = getattr(module, "__file__", None)
            if not file or not isinstance(file, str):
                continue
            rel = self.repo_rel(file)
            if rel is None:
                continue
            self.import_deps.add(rel)
            self._backfill_ast(name, module, rel)

    def _backfill_ast(self, name: str, module: ModuleType, rel: str) -> None:
        try:
            source = (self.root / rel).read_bytes()
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError):
            return
        package = getattr(module, "__package__", None) or (
            name if getattr(module, "__path__", None) is not None else name.rpartition(".")[0]
        )
        pkg_parts = package.split(".") if package else []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._record_present(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level and pkg_parts:
                    if node.level - 1 > len(pkg_parts):
                        continue
                    parts = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                    base = ".".join([*parts, base]) if base else ".".join(parts)
                if base:
                    self._record_present(base)
                for alias in node.names:
                    if base:
                        self._record_present(f"{base}.{alias.name}")
                    else:
                        self._record_present(alias.name)

    def _record_present(self, dotted: str) -> None:
        """A backfill candidate: only modules actually present count."""
        while dotted:
            module = sys.modules.get(dotted)
            if module is not None:
                file = getattr(module, "__file__", None)
                if file and isinstance(file, str):
                    rel = self.repo_rel(file)
                    if rel is not None:
                        self.import_deps.add(rel)
            if "." not in dotted:
                break
            dotted = dotted.rpartition(".")[0]

    # -- resolution -----------------------------------------------------

    def repo_rel(self, filename: str) -> str | None:
        """Repo-relative path for a filename, memoized per monitor."""
        if not filename or not isinstance(filename, str) or filename.startswith("<"):
            return None
        if not os.path.isabs(filename):
            return to_repo_rel(filename, self.root)
        try:
            return self._rel_cache[filename]
        except KeyError:
            rel = to_repo_rel(filename, self.root)
            self._rel_cache[filename] = rel
            return rel
