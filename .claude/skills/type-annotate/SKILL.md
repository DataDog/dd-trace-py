---
name: type-annotate
description: >
  Add complete type annotations to new or modified Python code in dd-trace-py.
  Use when writing new Python modules, when the user asks for type hints/annotations,
  or before committing production or test Python changes. Covers function signatures,
  locals, globals, class attributes, and validation via the lint skill.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
---

# Type-annotate Python (dd-trace-py)

## When to use

- New Python files or functions in PRs
- User asks to "type-annotate", "add hints", or "fix typing"
- After implementing a feature, before commit

## Standards (match existing ddtrace code)

1. **Import style** — separate `typing` imports per symbol (see `ddtrace/internal/excepthook.py`), not `from typing import *`.
2. **`from __future__ import annotations`** — use only when needed for forward refs; prefer quoted strings or `TYPE_CHECKING` blocks.
3. **Annotate everything new** in production code:
   - Function/method parameters and return types
   - Module-level globals (`_tool_id: Optional[int] = None`)
   - Class attributes when not inferred from `__init__`
   - Nested functions and closures used in hot paths when practical
4. **Tests** — annotate fixtures (`-> Iterator[...]`), helper classes, and callbacks; `pytest` fixtures return typed generators.
5. **Prefer precise types** — `CodeType`, `Callable[[...], T]`, `Optional[T]`, `dict[str, Any]`; use `object` over `Any` when any object is accepted.
6. **Version gates** — keep `if sys.version_info >= (3, 15):` blocks typed inside the branch.

## Workflow

```bash
# 1. See what changed in the PR slice
git diff <parent>..<branch> -- '*.py'

# 2. Edit files — add annotations to every new/changed def, global, class attr

# 3. Format + type-check only touched files (never raw mypy/ruff)
scripts/lint fmt -- path/to/file.py
scripts/lint typing -- path/to/file.py

# 4. Commit on the PR branch (one-liner message)
git commit -m "typing: annotate <scope>"
git push origin <branch>
```

For stacked PRs, work **bottom-up**: annotate PR N, push, rebase PR N+1 onto N, repeat.

## Common patterns

```python
# Module global
_active: dict[str, int] = {}

# Callback
def _on_event(code: CodeType, offset: int) -> Optional[object]:
    ...

# Fixture
@pytest.fixture()
def registered() -> Iterator[Callable[[CodeType, MonitoringEventHandler], None]]:
    ...

# TYPE_CHECKING for import cycles
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ddtrace.profiling.collector import Collector
```

## Do not

- Run `mypy` or `ruff` directly — use `scripts/lint`
- Add `# type: ignore` unless mypy requires it and a comment explains why
- Change runtime behavior while adding types

## Related

- Format/validate: `.claude/skills/lint/SKILL.md`
- Test changes: `.claude/skills/run-tests/SKILL.md`
