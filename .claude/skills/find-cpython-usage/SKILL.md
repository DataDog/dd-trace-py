---
name: find-cpython-usage
description: >
  Find all CPython internal headers and structs used in the codebase, particularly
  for profiling functionality. Use this when adding support for a new Python version
  to identify what CPython internals we depend on.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
---

# Inventory CPython dependencies

For a Python-version change, start with the native extensions listed in `setup.py` and their
CMake/Cargo inputs. Search tracked C, C++, Cython, and Rust sources, including declarations in
`.pxd` files and code under both `ddtrace/` and `src/`.

```bash
rg -n -g '*.{c,cc,cpp,h,hh,hpp,pyx,pxd,rs}' \
  'Python\.h|cpython[/.]|internal/pycore|pyo3(_ffi|::ffi)' ddtrace src
rg -n -g '*.{c,cc,cpp,h,hh,hpp,pyx,pxd,rs}' \
  'PyFrameObject|PyThreadState|PyInterpreterState|PyCodeObject|_PyInterpreterFrame' ddtrace src
rg -n -g '*.{c,cc,cpp,h,hh,hpp,pyx,pxd,rs}' \
  'PyGenObject|PyCoro_CheckExact|PyAsyncGenASend|FutureObj|TaskObj' ddtrace src
```

These searches are seeds, not an exhaustive API detector. Follow wrappers, aliases, generated
bindings, struct field reads, and version guards in the affected extensions. Distinguish vendored
code from code owned here, and public APIs from unstable internals.

Include generator/coroutine state, async-generator wrappers, and asyncio task/future layouts.
For examples of copied layouts and version guards, inspect
`ddtrace/internal/datadog/profiling/stack/echion/echion/cpython/tasks.h` and its callers.

Distinguish CPython's public `Include/` headers, CPython-specific `Include/cpython/` headers,
and private `Include/internal/pycore*.h` headers. Check the target version's guards and include
order for `Py_BUILD_CORE`; see `ddtrace/profiling/collector/_memalloc_frame.h` for a local example.

Within each affected extension, find field reads and layout-dependent copies as well as API calls:

```bash
rg -n -g '*.{c,cc,cpp,h,hh,hpp,pyx,pxd,rs}' -- \
  '(->|\.)(f_|co_|gi_|cr_|ag_|task_|future_)|offsetof\(|sizeof\(|copy_type\(' <affected-native-directory>
```

Follow each matching object's type back to its declaration, including aliases and locally copied
CPython structs. Check fields accessed through macros or helper functions too; the pattern above
does not cover every field name or access form.

Record each relevant header, symbol or field, source location, supported-version guard, and
lifetime/locking assumption. Pass that inventory to
[compare-cpython-versions](../compare-cpython-versions/SKILL.md); avoid maintaining a static symbol list.
