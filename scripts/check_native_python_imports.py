"""Fail if Rust code under src/native imports a ddtrace Python module.

The extension is built as a standalone package that must not depend on ddtrace. Python registers
the callables native needs instead (e.g. Context._register_tracestate_helpers), or native imports a
sibling module of its own package by a name derived at init (e.g. `_msgpack` in span/wire.rs), never
a literal ddtrace path.

This flags `py.import("ddtrace...")`, `PyModule::import(py, "ddtrace...")` and `.import(intern!(py,
"ddtrace..."))`, including calls whose earlier arguments contain nested parentheses such as
`import(m.py(), "ddtrace...")`. Strings (plain, byte and raw, e.g. r#"..."#) and comments (line,
and block including nested ones) are lexed together, so inert text can neither trigger the check —
a raw string containing a call-looking snippet — nor hide a real import — a `//` inside a URL
string literal. Char literals are masked so a paren inside one cannot confuse the call scan.

Run: scripts/lint native-import-check
"""

from collections.abc import Iterator
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
NATIVE_DIR = ROOT / "src" / "native"

# A raw or byte-raw string opening: r, b, br or rb (a word boundary keeps the r of an identifier
# like `logger` from reading as a prefix), then the hashes that size the closing delimiter.
RAW_STRING_OPEN = re.compile(r"\b(?:br|rb|b|r)(#*)\"")
CHAR_LITERAL = re.compile(r"'(?:[^'\\\n]|\\.)'")
IMPORT_CALL = re.compile(r"\bimport(?:_bound)?\s*\(")

# The packb import in span/wire.rs predates this check. The packer-split PR replaces it with the
# sibling _msgpack import and deletes this entry; until then the check still flags every other
# ddtrace import.
ALLOWED_IMPORTS = frozenset({"ddtrace.internal._encoding"})


def _blank(chars: list[str], start: int, end: int) -> None:
    # Keep newlines so reported line numbers stay right; the mask stays the same length as the
    # source so offsets in it can be mapped straight back to the file.
    for i in range(start, min(end, len(chars))):
        if chars[i] != "\n":
            chars[i] = " "


def _masked_views(source: str) -> tuple[str, str]:
    """Return two same-length masks of source for the two detection passes.

    code_view blanks comments and the contents of string and char literals, so only real code can
    match the import-call pattern. args_view blanks comments and char literals only, leaving the
    string literals readable so the arguments of a matched call can be extracted.
    """
    code = list(source)
    args = list(source)
    n = len(source)
    i = 0
    while i < n:
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end == -1 else end
            _blank(code, i, end)
            _blank(args, i, end)
            i = end
        elif source.startswith("/*", i):
            # Rust block comments nest.
            depth = 1
            j = i + 2
            while j < n and depth:
                if source.startswith("/*", j):
                    depth += 1
                    j += 2
                elif source.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            _blank(code, i, j)
            _blank(args, i, j)
            i = j
        elif source[i] == "'":
            # Mask char literals so a paren or quote inside one (e.g. ')' or '"') cannot leak into
            # either pass; a lone ' is a lifetime and is left alone.
            match = CHAR_LITERAL.match(source, i)
            if match is None:
                i += 1
                continue
            _blank(code, i + 1, match.end() - 1)
            _blank(args, i + 1, match.end() - 1)
            i = match.end()
        else:
            raw = RAW_STRING_OPEN.match(source, i)
            if raw is not None:
                closer = '"' + raw.group(1)
                end = source.find(closer, raw.end())
                end = n if end == -1 else end + len(closer)
                _blank(code, raw.end(), end - len(closer))
                i = end
                continue
            if source[i] != '"':
                i += 1
                continue
            # Plain or byte string: scan for the closing quote, honoring escapes.
            j = i + 1
            while j < n and source[j] != '"':
                j += 2 if source[j] == "\\" else 1
            end = min(j + 1, n)
            _blank(code, i + 1, j)
            i = end
    return "".join(code), "".join(args)


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
            end = i + 1
            while end < len(source) and source[end] != '"':
                end += 2 if source[end] == "\\" else 1
            if end >= len(source):
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
        source = path.read_text(encoding="utf-8")
        code_view, args_view = _masked_views(source)
        for match in IMPORT_CALL.finditer(code_view):
            for literal in _argument_literals(args_view, match.end()):
                if literal.startswith("ddtrace") and literal not in ALLOWED_IMPORTS:
                    try:
                        rel = path.relative_to(ROOT)
                    except ValueError:
                        # The root is outside the repository (tests scan a temporary copy).
                        rel = path.relative_to(root)
                    line = source.count("\n", 0, match.start()) + 1
                    violations.append(f"{rel}:{line}: imports a ddtrace module")
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
