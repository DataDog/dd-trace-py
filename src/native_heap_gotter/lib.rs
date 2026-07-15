// Copyright 2025-Present Datadog, Inc. https://www.datadoghq.com/
// SPDX-License-Identifier: Apache-2.0

//! Thin cdylib that re-exports libdatadog's `libdd-profiling-heap-gotter-ffi`
//! under stable, ddtrace-owned C symbols. The Python ctypes activator
//! (`ddtrace/internal/datadog/profiling/heap_gotter`) dlopen's the resulting
//! `libdd_heap_gotter.<ext-suffix>.so` and drives GOT-based native heap
//! profiling from it.
//!
//! The upstream FFI surface returns `ddog_VoidResult` (a `#[repr(C)]` tagged
//! union) from `install`; decoding that from ctypes is awkward. Here we fold
//! install + is-installed into a single `bool`-returning entry point so the
//! Python side gets a trivial success signal and never touches the union.
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
/// Combines `ddog_heap_gotter_install` + `ddog_heap_gotter_is_installed` so the
/// caller gets a simple `bool` and never has to decode `ddog_VoidResult`.
/// Idempotent: safe to call more than once (e.g. after `fork()`).
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_install() -> bool {
    // `ddog_heap_gotter_install` is `#[must_use]` and returns a VoidResult that
    // owns any error string. Bind it so it drops at end of scope (freeing that
    // string); success is confirmed via `is_installed` below rather than by
    // decoding the union.
    let _result = libdd_profiling_heap_gotter_ffi::ddog_heap_gotter_install();
    libdd_profiling_heap_gotter_ffi::ddog_heap_gotter_is_installed()
}

/// Return whether heap GOT overrides are currently installed in this process.
/// Always `false` on non-Linux targets.
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_is_installed() -> bool {
    libdd_profiling_heap_gotter_ffi::ddog_heap_gotter_is_installed()
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
    libdd_profiling_heap_gotter_ffi::ddog_heap_gotter_test_hook_hits()
}
