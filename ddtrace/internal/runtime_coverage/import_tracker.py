"""
Import graph tracker for runtime reachability analysis.

Tracks *why* each module was imported by recording (importer, imported, line)
edges. Works by wrapping ``ModuleCodeCollector.transform`` and
``after_import`` on the instance — all mutations are confined to this module.

AIDEV-NOTE: The tracker relies on a nesting stack (push in transform, pop in
after_import / exception hook) to determine the *parent* module, and walks
``sys._getframe()`` to locate the import line number in the parent.
"""

import dataclasses
from pathlib import Path
import sys
import typing as t

from ddtrace.internal.logger import get_logger


if t.TYPE_CHECKING:
    from types import CodeType
    from types import ModuleType

    from ddtrace.internal.coverage.code import ModuleCodeCollector

log = get_logger(__name__)


@dataclasses.dataclass
class ImportEdge:
    """A single directed edge in the import graph."""

    imported: str  # relative path of the imported module
    importer: str  # relative path of the importing module
    line: t.Optional[int]  # line number in the importer file (None if unknown)


class ImportGraphTracker:
    """Tracks the import graph by monkey-patching a ModuleCodeCollector instance."""

    def __init__(self, include_paths: list[Path], workspace_path: Path) -> None:
        self._include_paths = include_paths
        self._workspace_path = workspace_path
        self._import_stack: list[str] = []  # absolute paths of modules being imported
        self._edges: list[ImportEdge] = []

    # -- public API -----------------------------------------------------------

    def install(self, mcc: "ModuleCodeCollector") -> None:
        """Wrap *mcc*.transform and *mcc*.after_import on the instance."""
        original_transform = mcc.transform
        original_after_import = mcc.after_import

        tracker = self  # prevent closure over ``self`` name collision

        def wrapped_transform(code: "CodeType", _module: "ModuleType") -> "CodeType":
            tracker._on_transform(code)
            return original_transform(code, _module)

        def wrapped_after_import(_module: "ModuleType") -> None:
            original_after_import(_module)
            tracker._on_after_import(_module)

        # Monkey-patch the *instance* (not the class) so other consumers are
        # unaffected.
        mcc.transform = wrapped_transform  # type: ignore[assignment]
        mcc.after_import = wrapped_after_import  # type: ignore[assignment]

        # Register an exception hook so the stack stays balanced when an import
        # fails midway.
        from ddtrace.internal.coverage.code import ModuleCodeCollector as MCC

        MCC.register_import_exception_hook(lambda _name: True, self._on_import_exception)

    def get_import_graph(self) -> list[dict[str, t.Any]]:
        """Return edges as a list of plain dicts ready for JSON serialisation."""
        return [dataclasses.asdict(e) for e in self._edges]

    # -- internal callbacks ---------------------------------------------------

    def _is_tracked(self, abs_path: str) -> bool:
        p = Path(abs_path)
        return any(p.is_relative_to(ip) for ip in self._include_paths)

    def _rel(self, abs_path: str) -> str:
        try:
            return str(Path(abs_path).relative_to(self._workspace_path))
        except ValueError:
            return abs_path

    def _find_import_line(self, parent_abs: str) -> t.Optional[int]:
        """Walk the call stack to find the line in *parent_abs* that triggered the import."""
        try:
            frame = sys._getframe(0)
        except AttributeError:
            return None

        while frame is not None:
            if frame.f_code.co_filename == parent_abs:
                return frame.f_lineno
            frame = frame.f_back
        return None

    def _on_transform(self, code: "CodeType") -> None:
        from ddtrace.internal.utils.inspection import resolved_code_origin

        abs_path = str(resolved_code_origin(code))

        if not self._is_tracked(abs_path):
            return

        parent_abs: t.Optional[str] = self._import_stack[-1] if self._import_stack else None

        if parent_abs is not None and self._is_tracked(parent_abs):
            line = self._find_import_line(parent_abs)
            self._edges.append(
                ImportEdge(
                    imported=self._rel(abs_path),
                    importer=self._rel(parent_abs),
                    line=line,
                )
            )

        self._import_stack.append(abs_path)

    def _on_after_import(self, _module: "ModuleType") -> None:
        if not self._import_stack:
            return
        mod_file = getattr(_module, "__file__", None)
        if mod_file is not None and self._import_stack[-1] == mod_file:
            self._import_stack.pop()

    def _on_import_exception(self, _exc: t.Any, _module: "ModuleType") -> None:
        mod_file = getattr(_module, "__file__", None)
        if mod_file is not None and self._import_stack and self._import_stack[-1] == mod_file:
            self._import_stack.pop()
