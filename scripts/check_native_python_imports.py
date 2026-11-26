"""Fail if Rust code under src/profiling_ffi imports a ddtrace Python module.

The tracer's native extension is built as the standalone ddtrace-native package (its crate lives in
datadog-apm-python); what remains here is the profiling FFI cdylib. Both must not depend on ddtrace.
Python registers the callables native needs instead (e.g. Context._register_tracestate_helpers), or
native imports a sibling module of its own package by a name derived at init (e.g. `_msgpack` in
span/wire.rs), never a literal ddtrace path.

This flags `py.import("ddtrace...")`, `PyModule::import(py, "ddtrace...")` and `.import(intern!(py,
"ddtrace..."))`. Comments are ignored, and so are plain string literals such as the pyclass
`module = "ddtrace..."` attribute and event names.

Run: scripts/lint native-import-check
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
NATIVE_DIR = ROOT / "src" / "profiling_ffi"

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
IMPORT_DDTRACE = re.compile(r'\bimport(?:_bound)?\s*\([^)]*?"ddtrace\b')


def _blank_comment(match: re.Match[str]) -> str:
    # Keep the newlines so reported line numbers stay right.
    return "\n" * match.group().count("\n")


def find_violations(root: Path = NATIVE_DIR) -> list[str]:
    violations = []
    for path in sorted(root.rglob("*.rs")):
        source = COMMENT.sub(_blank_comment, path.read_text(encoding="utf-8"))
        for match in IMPORT_DDTRACE.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(ROOT)}:{line}: imports a ddtrace module")
    return violations


def main() -> int:
    violations = find_violations()
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(
            "src/profiling_ffi must not import ddtrace modules: have Python register the callable with native, "
            "or import a sibling module of the extension's own package instead.",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
