// Copyright 2025-Present Datadog, Inc. https://www.datadoghq.com/
// SPDX-License-Identifier: Apache-2.0

//! Thin cdylib wrapping `libdd-profiling-heap-gotter` (crates.io) under stable
//! `extern "C"` symbols for the Python ctypes activator to dlopen.

/// Install GOT overrides for supported heap-allocation symbols and report
/// whether the install actually took effect.
///
/// Returns the result of `install_heap_overrides`, i.e. whether at least one
/// allocator symbol's GOT entry was resolved and patched (so hooks will run).
///
/// After a successful install, a `fork()` child inherits the mapping and the
/// patched GOT, so a second native install is usually unnecessary. Upstream has
/// no `pthread_atfork` child reset for the process-global registry mutex
/// (`GLOBAL_OVERRIDES`); forking during an in-flight `install()`/`update()` can
/// leave that mutex locked in the child — treat mid-install fork as unsafe.
/// Prefer installing on the main thread (or in the worker after fork). The
/// Python activator's `_armed` skip avoids re-entering this cdylib after a
/// successful install on the common post-fork path; that is not a claim that
/// all inherited native state is fork-safe.
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_install() -> bool {
    libdd_profiling_heap_gotter::install_heap_overrides()
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

/// Set the mean sample distance (bytes between samples) for the heap sampler.
/// Must be called before `ddtrace_heap_gotter_install` to take effect.
///
/// # Safety
///
/// C ABI entry point; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_set_sampling_distance(distance: u64) {
    libdd_profiling_heap_gotter::set_default_sampling_distance(distance);
}

/// Re-scan loaded libraries and patch any newly-introduced GOT entries.
/// Normally called automatically from the internal `dlopen` hook; exposed here
/// for cases where the Python side loads a `.so` and wants immediate coverage.
///
/// # Safety
///
/// C ABI entry point with no arguments and no pointers; always safe to call.
#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_update() {
    libdd_profiling_heap_gotter::update_heap_overrides();
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
