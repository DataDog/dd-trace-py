"""Generate the dd-trace-py side of a module extraction: the bridge scaffold.

Companion to ``scripts/check_bridged_modules.py`` (the gate that keeps the
generated pieces in sync). Produces every mechanical edit the bridge pattern
requires; judgment calls (suite selection, consumer audit, PR text) stay with
the author. See the ``bridge-extracted-module`` skill for the full workflow.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable
from typing import Optional
import uuid


RENO_NOTE = """---
upgrade:
  - |
    ddtrace now depends on the ``{distribution}`` package, which is installed
    automatically when upgrading. Environments that install ddtrace from a private
    mirror or vendored wheelhouse must also mirror ``{distribution}``.
"""

_REQUIREMENTS_TO_CSV = [
    "uv",
    "run",
    "--no-project",
    "--with",
    "toml==0.10.2",
    "python",
    "scripts/requirements_to_csv.py",
]


def public_names(tree: ast.Module) -> list[str]:
    """Top-level public names defined by a module (the names a bridge re-exports)."""
    names = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [name for name in names if not name.startswith("_")]


def bridge_source(source: str, names: list[str]) -> str:
    """The bridge module content for an extracted ``source`` module.

    The ``x as x`` redundant-alias form is required by the repo's
    ``no_implicit_reexport = true`` mypy config: a plain re-import is not a
    re-export, so every explicitly re-exported name must be listed in alias
    form (the star import covers everything else at runtime).
    """
    leaf = source.rsplit(".", 1)[-1]
    lines = ['"""Bridge to the extracted %s package."""' % leaf, ""]
    lines.append("from %s import *  # noqa: F401,F403 -- re-export" % source)
    lines.extend(
        "from %s import %s as %s  # noqa: F401 -- re-export for type checkers" % (source, name, name) for name in names
    )
    return "\n".join(lines) + "\n"


def _entry_name(line: str) -> Optional[str]:
    """Dependency name from a quoted TOML list entry, or None for non-entries."""
    stripped = line.strip()
    if not stripped.startswith('"'):
        return None
    match = re.match(r'"([^"]*)"', stripped)
    if match is None:
        return None
    entry = match.group(1).split(";", 1)[0].strip()
    name = re.match(r"([A-Za-z0-9._-]+)", entry)
    return name.group(1) if name else None


def insert_dependency(text: str, section: str, header: str, entry: str) -> str:
    """Insert ``entry`` into a TOML string-array list, name-sorted, preserving comments.

    Text-splice on purpose: a TOML round-trip would drop the comments and
    formatting of ``pyproject.toml``.
    """
    lines = text.splitlines(keepends=True)
    current = None
    list_start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            continue
        if current == section and stripped.startswith(header):
            list_start = index
            break
    if list_start is None:
        raise ValueError("no %r list found in section [%s]" % (header, section))

    # Locate the array bounds and the quoted entries inside it.
    end = None
    entry_indices = []
    for index in range(list_start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("]"):
            end = index
            break
        if _entry_name(lines[index]) is not None:
            entry_indices.append(index)
    if end is None:
        raise ValueError("unterminated %r list in section [%s]" % (header, section))

    name = _entry_name('"%s"' % entry)
    if name is None:
        raise ValueError("cannot parse dependency name from %r" % entry)

    # Comments between entries belong to the entry that follows them, so the
    # insert position is the first entry whose name sorts after the new one.
    insert_at = end
    for index in entry_indices:
        existing = _entry_name(lines[index])
        if existing is not None and existing.casefold() > name.casefold():
            insert_at = index
            break

    new_line = '    "%s",' % entry
    if insert_at == end and entry_indices:
        previous = lines[insert_at - 1]
        if not previous.rstrip().endswith(","):
            lines[insert_at - 1] = previous.rstrip("\n") + ",\n"
    lines.insert(insert_at, new_line + "\n")
    return "".join(lines)


def bridge_test_source(module: str, source: str, anchor: str) -> str:
    """The bridge resolution test for one bridged module."""
    return f"""from {module} import {anchor}


def test_bridge_resolves_to_extracted_package():
    from {source} import {anchor} as Extracted{anchor}

    assert {anchor} is Extracted{anchor}
"""


@dataclass
class BridgePlan:
    """Everything ``generate`` produces, for callers that want to inspect or test it."""

    bridge: Path
    pyproject: Path
    test: Path
    reno: Path
    reexports: list[str]


def generate(
    module_path: Path,
    distribution: str,
    source: str,
    version: str,
    root: Path,
    run_command: Callable[[list[str]], int],
) -> BridgePlan:
    """Write the full bridge scaffold for a vendored module being bridged.

    ``run_command`` executes repo-rooted commands (requirements.csv
    regeneration); it is injected so tests can capture instead of spawn.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names = public_names(tree)
    if not names:
        raise ValueError("%s defines no public names to re-export" % module_path)

    module_path.write_text(bridge_source(source, names), encoding="utf-8")

    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text = insert_dependency(text, "project", "dependencies = [", "%s>=%s" % (distribution, version))
    text = insert_dependency(text, "dependency-groups", "lint = [", "%s==%s" % (distribution, version))
    pyproject.write_text(text, encoding="utf-8")

    if run_command(_REQUIREMENTS_TO_CSV) != 0:
        raise RuntimeError("requirements_to_csv failed; run it manually and commit the result")

    module = ".".join(module_path.relative_to(root).with_suffix("").parts)
    anchor = names[0]
    test = root / "tests" / module_path.relative_to(root / "ddtrace").parent / ("test_%s.py" % module_path.stem)
    test.parent.mkdir(parents=True, exist_ok=True)
    if test.is_file():
        with open(test, "a", encoding="utf-8") as f:
            f.write("\n\n" + bridge_test_source(module, source, anchor).lstrip("\n"))
    else:
        test.write_text(bridge_test_source(module, source, anchor), encoding="utf-8")

    reno = root / "releasenotes" / "notes" / ("bridge-%s-%s.yaml" % (module_path.stem, uuid.uuid4().hex[:16]))
    reno.parent.mkdir(parents=True, exist_ok=True)
    reno.write_text(RENO_NOTE.format(distribution=distribution), encoding="utf-8")

    return BridgePlan(bridge=module_path, pyproject=pyproject, test=test, reno=reno, reexports=names)
