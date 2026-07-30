// Copyright 2025-Present Datadog, Inc. https://www.datadoghq.com/
// SPDX-License-Identifier: Apache-2.0

//! C FFI bindings for [`libdd_profiling_heap_gotter`]. Mirrors upstream
//! `libdatadog/libdd-profiling-heap-gotter-ffi/src/lib.rs` (v37.0.0).

#![cfg_attr(not(test), deny(clippy::panic))]
#![cfg_attr(not(test), deny(clippy::unwrap_used))]
#![cfg_attr(not(test), deny(clippy::expect_used))]
#![cfg_attr(not(test), deny(clippy::todo))]
#![cfg_attr(not(test), deny(clippy::unimplemented))]
#![cfg_attr(not(test), deny(clippy::unreachable))]

use function_name::named;
use libdd_common_ffi::{wrap_with_void_ffi_result, VoidResult};

/// Install GOT overrides for supported heap-allocation symbols in the current process.
///
/// Installation is permanent: there is no un-install (see [`libdd_profiling_heap_gotter`]
/// for why). GOT entries are patched to point at functions in this library, so
/// the library containing these hooks must remain loaded for the life of the
/// process; unloading it would leave dangling function pointers.
///
/// On non-Linux targets this returns an error indicating that nothing
/// could be installed; the rest of the API can still be called safely.
#[no_mangle]
#[must_use]
#[named]
pub extern "C" fn ddog_heap_gotter_install() -> VoidResult {
    wrap_with_void_ffi_result!({
        let installed = libdd_profiling_heap_gotter::install_heap_overrides();
        anyhow::ensure!(installed, "no heap GOT overrides could be installed");
    })
}

/// Re-scan loaded libraries and patch newly-introduced GOT entries.
///
/// This is normally called automatically by the installed `dlopen` hook, but language runtimes may
/// call it explicitly after unusual native-extension loading flows. No-op on non-Linux targets.
#[no_mangle]
#[must_use]
#[named]
pub extern "C" fn ddog_heap_gotter_update() -> VoidResult {
    wrap_with_void_ffi_result!({
        libdd_profiling_heap_gotter::update_heap_overrides();
    })
}

/// Return whether heap GOT overrides are currently installed in this process. Always `false` on
/// non-Linux targets.
#[no_mangle]
#[must_use]
pub extern "C" fn ddog_heap_gotter_is_installed() -> bool {
    libdd_profiling_heap_gotter::heap_overrides_are_installed()
}

/// Test-only: number of times a patched hook (`malloc`/`free`) has run in
/// this process. Lets integration tests prove the patched GOT was actually
/// exercised, not just that nothing crashed. Not part of the production API
/// surface; only compiled in with the `test-support` feature.
#[cfg(feature = "test-support")]
#[no_mangle]
#[must_use]
pub extern "C" fn ddog_heap_gotter_test_hook_hits() -> u64 {
    libdd_profiling_heap_gotter::test_hook_hits()
}
