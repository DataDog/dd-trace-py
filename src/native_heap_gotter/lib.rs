// Copyright 2025-Present Datadog, Inc. https://www.datadoghq.com/
// SPDX-License-Identifier: Apache-2.0

#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_install() -> bool {
    let _result = libdd_profiling_heap_gotter_ffi::ddog_heap_gotter_install();
    libdd_profiling_heap_gotter_ffi::ddog_heap_gotter_is_installed()
}

#[no_mangle]
pub extern "C" fn ddtrace_heap_gotter_is_installed() -> bool {
    libdd_profiling_heap_gotter_ffi::ddog_heap_gotter_is_installed()
}
