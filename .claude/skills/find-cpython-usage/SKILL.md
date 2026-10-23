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
  'Python\.h|cpython/|internal/pycore|pyo3(_ffi|::ffi)' ddtrace src
rg -n -g '*.{c,cc,cpp,h,hh,hpp,pyx,pxd,rs}' \
  'PyFrameObject|PyThreadState|PyInterpreterState|PyCodeObject|_PyInterpreterFrame' ddtrace src
```

These searches are seeds, not an exhaustive API detector. Follow wrappers, aliases, generated
bindings, struct field reads, and version guards in the affected extensions. Distinguish vendored
code from code owned here, and public APIs from unstable internals.

Record each relevant header, symbol or field, source location, supported-version guard, and
lifetime/locking assumption. Pass that inventory to
[compare-cpython-versions](../compare-cpython-versions/SKILL.md); avoid maintaining a static symbol list.
