# Python 3.15 profiling stack (6 PRs)

**Vintage:** Sun 2026-08-30 13:37 EDT (−0400). Live tips after restack onto #19903. #19267 base is #19903 (`vlad/py315-pyo3-contextvar`), **not** `main` and **not** #19861. Not merge-ready.

This map is the **profiling deep-test** stack (DoE attach: #19269 / #19270 / #19272). Track B forks at [#19903](https://github.com/DataDog/dd-trace-py/pull/19903) (`PyContextVar_*` shim). [#19861](https://github.com/DataDog/dd-trace-py/pull/19861) stays the unofficial wheels ancestor under #19903. Unofficial mega **above** #19903 (`#19907 ← #19904 ← #19906 ← …`) is not this stack and was not rebased.

Live chain: **#19861 ← #19903 ← #19267 ← #19269 ← #19270 ← #19272 ← #19947 ← #19273**.

[#19910](https://github.com/DataDog/dd-trace-py/pull/19910) **merged** on `main` (`3e4b91aba1`). This stack was **not** rebased onto it — a profiling wheel does **not** contain the wrap lift. [#17849](https://github.com/DataDog/dd-trace-py/pull/17849) is on `main` and is not this stack. Do not open `vlad/315-official-support`.

## Merge order (bottom → top)

| # | PR | Branch | Base branch | Tip SHA (origin) |
|---|-----|--------|-------------|------------------|
| — | [#19861](https://github.com/DataDog/dd-trace-py/pull/19861) | `vlad/py315-manylinux-wheels` | `vlad/py315-lib-injection-ssi` | `cac61cabb5` |
| — | [#19903](https://github.com/DataDog/dd-trace-py/pull/19903) | `vlad/py315-pyo3-contextvar` | `vlad/py315-manylinux-wheels` | `df11b57783` |
| 1 | [#19267](https://github.com/DataDog/dd-trace-py/pull/19267) | `vlad/315-peripheral-compat` | `vlad/py315-pyo3-contextvar` | `cebad2e825` |
| 2 | [#19269](https://github.com/DataDog/dd-trace-py/pull/19269) | `vlad/ddtracepy-315-profiling-native` | `vlad/315-peripheral-compat` | `9d29b1aeb5` |
| 3 | [#19270](https://github.com/DataDog/dd-trace-py/pull/19270) | `vlad/ddtracepy-315-profiling-collectors` | `vlad/ddtracepy-315-profiling-native` | `c303996a5c` |
| 4 | [#19272](https://github.com/DataDog/dd-trace-py/pull/19272) | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `vlad/ddtracepy-315-profiling-collectors` | `8429205513` |
| 5 | [#19947](https://github.com/DataDog/dd-trace-py/pull/19947) | `vlad/315-profiling-test-install-subdir` | `vlad/ddtracepy-315-profiling-asyncio-monitoring` | `203555dbbf` |
| 6 | [#19273](https://github.com/DataDog/dd-trace-py/pull/19273) | `vlad/315-profiling-dev-tooling` | `vlad/315-profiling-test-install-subdir` | this branch tip |

**Closed (absorbed earlier):** [#19268](https://github.com/DataDog/dd-trace-py/pull/19268) → #19269 · [#19271](https://github.com/DataDog/dd-trace-py/pull/19271) → #19270

**Not in this stack:** wrap #19910 (on main); unofficial `#19907 ← #19904 ← #19906 ← #19928 ← #19942 ← #19943`; IMAGE_TAG [#19936](https://github.com/DataDog/dd-trace-py/pull/19936); closed [#19254](https://github.com/DataDog/dd-trace-py/pull/19254).

## Layer table

| Layer | Meaning | Which PR |
| --- | --- | --- |
| Compiled into artifact | Native C++/Rust 3.15 ABI, cmake tests | #19269 |
| Test install layout | 3.15 gtest under `test/py315` | #19947 |
| Armed at runtime | Collectors + setup.py native compile + riot/CI matrix | #19270 |
| Observable in product/Python | sys.monitoring asyncio path; `wrap()` stays below 3.15 | #19272 |
| Docs / bring-up | Runbook, verify script, this file | #19273 |
| Peripheral (logging / FunctionStore) | Version warning + restore_all unwrap | #19267 |
| Limited-API shim (parent) | `PyContextVar_*` local decls | #19903 |

#19272 is **not** the wrap lift. Do not attach DoE/AB to #19273.

## Validation legs (314 vs 315)

Version is the knob; wheel SHA stays fixed. Both 3.14 and 3.15 DoE use the **same #19272 tip**.

- **Wrap-free DoE** (alloc isolated / full-stack / bytearray): **#19272** `8429205513` only. Natives-only fallback: **#19270** `c303996a5c`.
- **Wrap-sensitive TD** (`ai_gateway`): needs #19910 **in the wheel** (main, or rebase).
- Attach results to **#19269 / #19270 / #19272**. Never #19273.
- prof-correctness 3.15 is **S3-wheel only** (`install.sh`). Needs #19936 + pc #191 + #19269+#19270. Residual: `requires-python >=3.9,<3.15`, no `--ignore-requires-python`. Do not merge #19880 to unblock it.

Sign-off: prof-correctness → DoE alloc isolated → full-stack → `rapid_python_http_smoke_test` TD → `ai_gateway` TD (after wrap is in the wheel) → `ds-metrics-workers` temporal soak.
