# Native heap gotter embed design (dd-trace-py)

## Meta

| Field | Value |
| :---- | :---- |
| **Status** | **Exploratory / Proposed** — design record for review; not GA-ready and not a commitment to ship as documented |
| **Authors** | Vlad Scherbich (dd-trace-py profiling) |
| **Start date** | 2026-07-22 |
| **Primary reviewers** | Profiling Python team; FH / eBPF profiler (Scott Gerring) for USDT contract |
| **Related work** | [Native heap profiling roadmap](../../ddtrace/native-heap-profiling-roadmap.md) (living doc, not yet tracked in this repo); OTel memory-profiling design ([opentelemetry-ebpf-profiler#1672](https://github.com/open-telemetry/opentelemetry-ebpf-profiler/pull/1672)) |

### Publication

This ADR captures **exploratory embed design** for team review and PR discussion. It documents intent and trade-offs under active investigation in a draft PR stack. Nothing here is accepted for GA or customer release until explicitly promoted through review, staging evidence, and product alignment.

## Abstract

This record documents how dd-trace-py **explores** embedding Datadog's native heap sampler ("gotter") so Python processes can emit `ddheap:alloc` / `ddheap:free` USDT probes for the Full Host (FH) eBPF profiler. The Python tracer would **arm** the sampler at profiler startup; it would **not** collect, upload, or symbolize native heap profiles in-process. Phase 1 targets allocation-only activation; live-heap free probes are explored in follow-on draft PRs. Producer-side de-duplication against the in-process `_memalloc` sampler is **out of scope** for the current stack — while native (FH/eBPF) and runtime (in-process) profilers remain discrete tools, we should not selectively de-duplicate in the tracer; a unified profiler presentation effort should inform any future approach.

## Introduction

### Problem

CPython's in-process memory profiler (`_memalloc_heap`) samples the pymalloc-managed OBJ and (optionally) MEM domains. It does not see direct libc `malloc` traffic from C extensions, NumPy buffer allocation, pymalloc arena refills, or other RAW-domain paths. Customers running mixed Python/native workloads therefore miss a large fraction of heap cost in the managed-heap profile.

The FH eBPF profiler can consume out-of-process USDT probes on sampled native allocations and produce `ebpf-alloc-size`, `ebpf-alloc-samples`, and (with free probes) `ebpf-heap-live-size` profiles with native stack attribution. dd-trace-py must activate those probes without:

- loading GOT-patching machinery for every customer import of the tracer;
- double-counting allocations already sampled by `_memalloc` (if/when both producers run);
- coupling the gotter release cadence to the libdatadog git pin used by `_native.so` — **under exploration** via a separate cdylib (see Decision §1).

### Success criteria (dd-trace-py scope — aspirational, not GA gates)

- **Opt-in at build and runtime:** wheels ship the cdylib only when explicitly requested; runtime arming requires a separate config flag.
- **Zero overhead when disabled:** importing ddtrace or starting the profiler with the feature off must not `dlopen` the gotter or patch the GOT.
- **Fail-closed:** missing cdylib, unsupported platform, or install failure must not break profiler startup or managed-heap collection.
- **Contract compatibility:** emit Datadog's `ddheap:*` USDT provider expected by FH (`sgg/heap-prof-2` consumer), aligned with the broader OTel memory-profiling direction.

### Out of scope (FH / backend / UI)

- eBPF uprobe attachment, live-heap correlation maps, OTLP export, and back-pressure PID control (FH ebpf-profiler).
- Backend join of FH profiles to the Python service page, trampoline frame stripping, CompView gauge aggregation (profiling-backend / web-ui).
- Producer-side de-duplication / ownership partition between gotter and `_memalloc` — deferred pending unified profiler presentation.
- Runtime allocation hints (TLS / USDT arg extensions) — future complement, not a replacement for backend coordination.
- Trace → allocation correlation via the universal-profiling ABI (Phase 4).

## Decision (exploratory — draft PR stack)

### 1. Separate `libdd_profiling_heap_gotter_ffi` cdylib, not merged into `_native.so`

We **explore** shipping a standalone Rust **cdylib** built from `src/native_heap_gotter/`, staged as `liblibdd_profiling_heap_gotter_ffi<EXT_SUFFIX>.so` under `ddtrace/internal/datadog/profiling/`. Python would activate it through a ctypes **dlopen** activator (`ddtrace/internal/datadog/profiling/heap_gotter/__init__.py`) that calls the **libdatadog heap-gotter FFI** (`ddog_*` symbols, `VoidResult`):

| Symbol | Role |
| :----- | :--- |
| `ddog_heap_gotter_install()` | Install GOT overrides; returns `VoidResult` (`Ok` / `Err`) |
| `ddog_heap_gotter_update()` | Re-scan for newly loaded libraries (optional explicit call) |
| `ddog_heap_gotter_is_installed()` | Query install state (idempotent after `fork()`) |
| `ddog_heap_gotter_test_hook_hits()` | Test-only hit counter (`test-support` feature) |

The wrapper crate mirrors upstream `libdatadog/libdd-profiling-heap-gotter-ffi` (`src/native_heap_gotter/lib.rs`). Installation is **permanent and process-global**; the activator keeps the `ctypes.CDLL` handle alive for the process lifetime and never `dlclose`s.

**Implementation status:** under exploration on draft [PR #19078](https://github.com/DataDog/dd-trace-py/pull/19078) (FFI pivot at `6f7b424` — adopt libdatadog `ddog_*` ABI instead of ddtrace-owned `ddtrace_heap_gotter_*` symbols).

**Rationale:** GOT rewriting is invasive and process-wide. Merging it into `_native.so` would load the interposer on every tracer import and tie heap hooking to the main native extension's release cycle.

### 2. Lazy load: build opt-in, runtime opt-in

| Gate | Variable | Default | Effect |
| :--- | :------- | :------ | :----- |
| **Build** | `DD_PROFILING_NATIVE_HEAP_BUILD=1` | off | `setup.py` runs `cargo build --release` for `src/native_heap_gotter/` and packages the `.so` into the wheel (`BUILD_NATIVE_HEAP_GOTTER`). |
| **Runtime** | `DD_PROFILING_NATIVE_HEAP_ENABLED=true` | off | `Profiler._start_service()` calls `heap_gotter.install()` once at profiler start ([PR #19079](https://github.com/DataDog/dd-trace-py/pull/19079)). |

Importing `heap_gotter` only **dlopen**s the library (if present); it does **not** install hooks. Config registration lives in `ProfilingConfigNativeHeap` (`ddtrace/internal/settings/profiling.py`); if the cdylib is absent, the flag is cleared at import time (fail-closed).

Build remains opt-in so standard CI wheels and customer installs do not pay the staged artifact cost (~370 KiB `.text` on linux x86_64 release builds; macOS carries a stub that is never loaded in production).

### 3. USDT probes consumed by FH eBPF — no in-process collection

The gotter fires Datadog USDT probes:

| Probe | When | Consumer |
| :---- | :--- | :------- |
| `ddheap:alloc(user, size, weight)` | Sampled allocation | FH `uprobe_heap_alloc` → `ebpf-alloc-size` / `ebpf-alloc-samples` |
| `ddheap:free(ptr)` | Free of a previously sampled allocation | FH reconciliation → `ebpf-heap-live-size` (Phase 1b) |

dd-trace-py has **no upload path** for these events. Validation of end-to-end probe firing uses staging FH Test Drive (documented in the [roadmap](../../ddtrace/native-heap-profiling-roadmap.md)) or, in CI, the gotter `test_hook_hits` counter — not an in-process eBPF attach.

**Phase 1 scope (draft stack):** allocation-only (`ddheap:alloc`). The wrapper's `Cargo.toml` keeps `live-heap` off via `default-features = false` on the gotter dependency. Live-heap (`ddheap:free`) is explored on draft [PR #19325](https://github.com/DataDog/dd-trace-py/pull/19325).

### 4. Dependency isolation from `_native.so`

| Component | libdatadog source | Notes |
| :-------- | :---------------- | :---- |
| `_native.so` / `ddtrace-native` | git pin `v37.0.0` (`src/native/Cargo.toml`) | Monolith: profiling FFI, crashtracker, RC, etc. |
| `libdd_profiling_heap_gotter_ffi` cdylib | git pin `56dab857b` (`src/native_heap_gotter/Cargo.toml`) | Mirrors upstream `libdd-profiling-heap-gotter-ffi` landed after v37.0.0; re-pin when tagged. **Not** crates.io `libdd-profiling-heap-gotter` 1.0.0 (earlier ADR draft assumed that path; current draft stack uses libdatadog git FFI). |

Separate release cadence, smaller build graph, and no double-shipping libdatadog inside the main extension — **subject to re-pin / crates.io migration review**.

**Artifact naming:** Cargo produces `liblibdd_profiling_heap_gotter_ffi.so` (libdatadog double-`lib` convention); `setup.py` stages it under `ddtrace/internal/datadog/profiling/` with `EXT_SUFFIX`.

**Platform (Phase 1):** Linux x86_64 only. `setup.py` gates the gotter build on `CURRENT_OS == "Linux"` and `is_64_bit_python()`; the activator rejects non-Linux at import. arm64 gotter build deferred (upstream uses pointer-tagged allocation headers on arm64).

### 5. Coexistence with in-process `_memalloc` (no producer-side partition in stack)

When the gotter arms successfully, two independent Poisson samplers may both observe some allocations — notably the **large-object tail** where pymalloc delegates OBJ/MEM requests strictly greater than 512 bytes to glibc `malloc`. The gotter hooks `malloc` via GOT; `_memalloc_heap` hooks OBJ/MEM at `PyObject_Malloc` / `PyMem_Malloc`.

**Product alignment (Scott Gerring, 2026-07-31):** while native (FH/eBPF) and runtime (in-process) profilers remain **discrete tools** in Datadog, we should **not** selectively de-duplicate in the tracer. Ongoing work toward a **unified profiler view** should inform whether any producer-side coordination is appropriate. **Recommendation:** defer partition / de-dup design until that effort clarifies the UX.

Arming the gotter does **not** imply disabling the managed-heap collector — doing so would discard Python-managed visibility the gotter never produces.

## Validation approach

| Layer | Method | Ship gate? |
| :---- | :----- | :--------- |
| **Unit / wiring** | `tests/profiling/test_native_heap_gotter.py` (fail-closed, profiler wiring, install idempotence) | Exploratory CI |
| **Staging FH** | Test Drive A/B on `do_anomaly_api` / ai_gateway — proves USDT → eBPF pipeline | Complementary |
| **Arming metric** | Draft [PR #19376](https://github.com/DataDog/dd-trace-py/pull/19376) — **closed**; CI ownership-handoff test deemed sufficient |

**Cluster A/B findings (2026-07-29):** staging dedup campaigns on `do_anomaly_api` and ai_gateway were **inconclusive for quantified overlap bytes**. Workloads are dominated by RAW-domain NumPy/native allocations (eBPF-only in both arms); aggregate flamegraphs lack per-sample size; and JSON logging dropped ddtrace INFO arming lines. See `experimental/teams/profiling-python/ddtrace-upgrade/native_heap_dedup_ab_recap.md`. **Not sufficient alone to justify shipping producer-side de-dup.**

## Consequences

### Positive (if pursued)

- Customers who enable native heap profiling could get libc-level allocation visibility in FH profiles without changing application code.
- Disabled-by-default embed avoids GOT patching and wheel cost for the majority of installs.
- Separate cdylib decouples heap sampler updates from `_native` libdatadog v37 pin.

### Negative / trade-offs

- **Two heap profile lanes** until backend BE-1/BE-2 joins FH `alloc-size` to the Python service page (Phase 5).
- **Potential double-counting** when both gotter and `_memalloc` run — no producer-side partition in the current stack; backend / unified-view work must address presentation.
- **Permanent GOT patch** — cannot uninstall; library must remain mapped.
- **Fork inheritance** — child processes inherit patched GOT (re-install is idempotent no-op).
- **Platform gap** — no arm64 gotter in Phase 1; macOS builds carry an unused stub.
- **Flamegraph noise** — gotter trampoline frames rank highly until FE strip/roll-up.

### Operational

- Wheels for dogfood require GitLab pipeline with `DD_PROFILING_NATIVE_HEAP_BUILD=1`.
- FH heap-prof DaemonSet zone affinity can block A/B unless pods land on covered nodes.

## Alternatives considered

| Alternative | Why rejected or deferred |
| :---------- | :----------------------- |
| **Merge gotter into `_native.so`** | Every import loads GOT interposer; couples release cadence and wheel size to main native build. |
| **crates.io `libdd-profiling-heap-gotter` wrapper with `ddtrace_*` ABI** | Earlier draft; current stack uses libdatadog git FFI (`ddog_*`) on [PR #19078](https://github.com/DataDog/dd-trace-py/pull/19078). |
| **Disable in-process `_memalloc` when gotter arms** | Throws away Python-managed heap profile; gotter never sees ≤512B pool-served allocations. |
| **Producer-side ownership partition (size split at 512 B)** | Explored separately; on hold — selective de-dup in-tracer conflicts with discrete native/runtime profiler UX pending unified view. |
| **Shared sampler state across gotter and `_memalloc`** | Highest fidelity but most invasive; deferred. |
| **Backend-only dedup (Option A)** | Desirable long-term but insufficient if both producers emit overlapping samples without coordination. |
| **Rely on cluster A/B alone for ship proof** | Workload confounders make byte-level quantification inconclusive. |

## Deferred work

| Phase | Owner | Summary |
| :---- | :---- | :------ |
| **1b / E — live-heap** | dd-trace-py | `ddheap:free` producer — draft [PR #19325](https://github.com/DataDog/dd-trace-py/pull/19325) |
| **Producer-side de-dup / partition** | dd-trace-py + product | On hold pending unified profiler presentation |
| **Native retain-set cap soak** | FH (Scott) | Live-set tracking + cap/eviction in eBPF consumer |
| **3 — Attribution** | FH (+ dd-trace-py symbols) | Shared unwinder; Python frames for recognized CPython |
| **4 — Trace correlation** | dd-trace-py + FH | Export universal-profiling ABI |
| **5 — UI parity** | Backend / FE | Service-page join, trampoline strip, native Heap Live Size tabs |
| **GA follow-up** | dd-trace-py | Consolidate crate; default wheels; re-pin libdatadog tag |
| **arm64 gotter** | dd-trace-py | Separate build investigation |

## PR stack and code map

All PRs are **draft** and **exploratory** as of 2026-07-31:

`main` ← [#19078](https://github.com/DataDog/dd-trace-py/pull/19078) (cdylib + libdatadog FFI build) ← [#19079](https://github.com/DataDog/dd-trace-py/pull/19079) (activator + config + wiring) ← [#19325](https://github.com/DataDog/dd-trace-py/pull/19325) (live-heap).

| Path | Role |
| :--- | :--- |
| `src/native_heap_gotter/{Cargo.toml,lib.rs}` | Wrapper cdylib; `ddog_heap_gotter_*` FFI |
| `setup.py` | `build_heap_gotter()`, `BUILD_NATIVE_HEAP_GOTTER`, stages `liblibdd_profiling_heap_gotter_ffi*` |
| `ddtrace/internal/datadog/profiling/heap_gotter/__init__.py` | ctypes activator (fail-closed dlopen; `VoidResult` handling) |
| `ddtrace/internal/settings/profiling.py` | `ProfilingConfigNativeHeap`, availability gate |
| `ddtrace/profiling/profiler.py` | `_start_service()` arming |
| `tests/profiling/test_native_heap_gotter.py` | Wiring and smoke tests |
| `ddtrace/native-heap-profiling-roadmap.md` | Living tracker (local/untracked in repo; use PR links for canonical progress) |

## References

- [Native heap profiling roadmap](../../ddtrace/native-heap-profiling-roadmap.md) — living doc; may remain untracked until promoted
- [OTel eBPF profiler memory profiling design](https://github.com/open-telemetry/opentelemetry-ebpf-profiler/pull/1672)
- [libdatadog heap gotter FFI](https://github.com/DataDog/libdatadog/tree/main/libdd-profiling-heap-gotter-ffi) (git pin on draft #19078)
- Staging dedup A/B recap: `experimental/teams/profiling-python/ddtrace-upgrade/native_heap_dedup_ab_recap.md`
- Profiling native component overview: `ddtrace/internal/datadog/profiling/docs/Design.md`
