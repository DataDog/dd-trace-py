// Copyright 2025-Present Datadog, Inc. https://www.datadoghq.com/
// SPDX-License-Identifier: Apache-2.0

//! Thin cdylib that wraps libdatadog's published `libdd-profiling-heap-gotter`
//! crate under stable, ddtrace-owned C symbols. The Python ctypes activator
//! (`ddtrace/internal/datadog/profiling/heap_gotter`) dlopen's the resulting
//! `libdd_heap_gotter.<ext-suffix>.so` and drives GOT-based native heap
//! profiling from it.
//!
//! The upstream crate exposes a pure-Rust API (`install_heap_overrides`,
//! `heap_overrides_are_installed`); it is not a C-ABI surface. Here we re-export
//! those calls as fixed, unmangled `extern "C"` entry points returning a plain
//! `bool`, so the Python ctypes side links against stable symbol names and gets
//! a trivial success signal. We also expose
//! `ddtrace_heap_gotter_live_heap_enabled`, which reports whether this artifact
//! was built with live-heap tracking (Phase 2: `ddheap:free` + retain flagging).
//!
//! Installation is permanent and process-global: the GOT entries patched by
//! `install` point at functions inside the linked-in gotter code, so this
//! library must stay loaded for the life of the process. The Python activator
//! loads it into the global namespace and never unloads it. After `fork()` the
//! child inherits both the loaded library and the patched GOT, so a re-install
//! in the child is a harmless no-op.

/// Install GOT overrides for supported heap-allocation symbols and report
/// whether they are now active.
///
/// Combines `install_heap_overrides` + `heap_overrides_are_installed` so the
/// caller gets a simple `bool`. Idempotent: safe to call more than once (e.g.
/// after `fork()`).
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_install() -> bool {
    // `install_heap_overrides` returns whether at least one symbol was patched;
    // we ignore it and confirm success via `heap_overrides_are_installed` below
    // to preserve the original install-then-check contract.
    let _installed = libdd_profiling_heap_gotter::install_heap_overrides();
    libdd_profiling_heap_gotter::heap_overrides_are_installed()
}

/// Return whether heap GOT overrides are currently installed in this process.
/// Always `false` on non-Linux targets.
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_is_installed() -> bool {
    libdd_profiling_heap_gotter::heap_overrides_are_installed()
}

/// Report whether this cdylib was built with live-heap tracking (Phase 2:
/// the `ddheap:free` USDT + per-allocation retain flagging), so the free side
/// can be reconciled against allocations. This is a *compile-time* property of
/// the shipped artifact — alloc-only builds return `false`, live-heap builds
/// return `true` — and is NOT a runtime toggle. The Python activator surfaces
/// it so the profiler can report which native-heap mode is armed.
///
/// AIDEV-NOTE: `cfg!(feature = "live-heap")` here is our crate's own feature,
/// declared in Cargo.toml as a pure forward to
/// `libdd-profiling-heap-gotter/live-heap`. It is true iff the artifact was
/// built with `--features live-heap`, which is exactly the condition under
/// which the sampler compiled in DD_HEAP_LIVE_TRACKING and emits `ddheap:free`.
/// Keep this boolean in lockstep with the Cargo feature so the runtime signal
/// never disagrees with the ELF `.note.stapsdt` reality.
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_live_heap_enabled() -> bool {
    cfg!(feature = "live-heap")
}

/// Test-only: number of times a patched hook has run in this process. Lets
/// integration tests prove the patched GOT was actually exercised without a
/// live eBPF attach. Only present when built with the `test-support` feature;
/// never compiled into shipped wheels.
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[cfg(feature = "test-support")]
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_test_hook_hits() -> u64 {
    libdd_profiling_heap_gotter::test_hook_hits()
}
