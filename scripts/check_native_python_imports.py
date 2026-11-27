"""Fail if Rust code under src/native imports a ddtrace Python module.

The extension is built as a standalone package that must not depend on ddtrace. Python registers
the callables native needs instead (e.g. Context._register_tracestate_helpers), or native imports a
sibling module of its own package by a name derived at init (e.g. `_msgpack` in span/wire.rs), never
a literal ddtrace path.

This flags `py.import("ddtrace...")`, `PyModule::import(py, "ddtrace...")` and `.import(intern!(py,
"ddtrace..."))`, including calls whose earlier arguments contain nested parentheses such as
`import(m.py(), "ddtrace...")`. Comments are ignored, and so are plain string literals such as the pyclass
`module = "ddtrace..."` attribute and event names.

Run: scripts/lint native-import-check
"""

from collections.abc import Iterator
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
NATIVE_DIR = ROOT / "src" / "native"

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
IMPORT_CALL = re.compile(r"\bimport(?:_bound)?\s*\(")


def _blank_comment(match: re.Match[str]) -> str:
    # Keep the newlines so reported line numbers stay right.
    return "\n" * match.group().count("\n")


def _argument_literals(source: str, start: int) -> Iterator[str]:
    """Yield every string literal among the arguments of the call whose "(" ends at "start".

    An earlier argument can itself contain a closing parenthesis, such as the receiver in
    "import(m.py(), "ddtrace...")" or a macro like "intern!(py, "ddtrace...")", so the call ends
    where the parenthesis depth returns to zero, not at the first ")".
    """
    depth = 1
    i = start
    while i < len(source):
        char = source[i]
        if char == '"':
            end = source.find('"', i + 1)
            if end == -1:
                return
            yield source[i + 1 : end]
            i = end + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return
        i += 1


def find_violations(root: Path = NATIVE_DIR) -> list[str]:
    violations = []
    for path in sorted(root.rglob("*.rs")):
        source = COMMENT.sub(_blank_comment, path.read_text(encoding="utf-8"))
        for match in IMPORT_CALL.finditer(source):
            for literal in _argument_literals(source, match.end()):
                if literal.startswith("ddtrace"):
                    line = source.count("\n", 0, match.start()) + 1
                    violations.append(f"{path.relative_to(ROOT)}:{line}: imports a ddtrace module")
                    break
    return violations


def main() -> int:
    violations = find_violations()
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(
            "src/native must not import ddtrace modules: have Python register the callable with native, "
            "or import a sibling module of the extension's own package instead.",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
