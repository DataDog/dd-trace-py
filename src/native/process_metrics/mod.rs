//! Native process-metrics collection. Every call queries the *current* process
//! fresh -- there is no cached PID or handle, so results are correct
//! immediately after `fork()` without any forksafe hook.

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
mod unsupported;
#[cfg(target_os = "windows")]
mod windows;

#[cfg(target_os = "linux")]
use linux as platform;
#[cfg(target_os = "macos")]
use macos as platform;
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
use unsupported as platform;
#[cfg(target_os = "windows")]
use windows as platform;

use pyo3::prelude::*;
use pyo3::types::PyModule;

/// Process-level metrics for the current process, read fresh on every call.
///
/// Returns `(cpu_time_sys_ns, cpu_time_user_ns, ctx_switches_voluntary,
/// ctx_switches_involuntary, num_threads, rss_bytes)`. `ctx_switches_*` are
/// `-1` when the platform cannot report them -- e.g. macOS involuntary ctx
/// switches (see `macos.rs`), older Linux kernels lacking
/// `nonvoluntary_ctxt_switches` (see `linux.rs`), or any metric on a
/// platform without a native implementation at all (see `unsupported.rs`,
/// which instead raises before returning a tuple).
#[pyfunction]
fn process_metrics() -> PyResult<(u64, u64, i64, i64, u64, u64)> {
    platform::process_metrics()
}

/// Total memory available to the system (physical RAM + swap), in bytes.
#[pyfunction]
fn total_memory_bytes() -> PyResult<u64> {
    platform::total_memory_bytes()
}

pub fn register_process_metrics(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(total_memory_bytes, m)?)?;
    Ok(())
}
