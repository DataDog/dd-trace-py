//! macOS process metrics, modelled on `psutil/arch/osx/proc.c`
//! (`psutil_proc_pidtaskinfo_oneshot`) and `_psosx.py`.
//!
//! Unlike the Mach `task_info`/`task_threads` path used elsewhere in psutil
//! for per-thread enumeration, the metrics we need (cpu times, rss, thread
//! count, ctx switches) are all available from a single
//! `proc_pidinfo(PROC_PIDTASKINFO)` call (from `<libproc.h>`) -- no
//! `task_for_pid`/`vm_deallocate` ownership dance required, since we only
//! ever query our own process.
//!
//! Ctx switches: `pti_csw` is the same field psutil itself reads for
//! `num_ctx_switches().voluntary` (`pidtaskinfo_map['volctxsw']` in
//! `_psosx.py`); there is no separate involuntary count here. psutil reports
//! involuntary as a hardcoded 0 because it distrusts `getrusage()`'s
//! `ru_nivcsw` on macOS ("Unvoluntary value seems not to be available") --
//! external research corroborates this (e.g. libuv's `uv_getrusage` docs
//! note BSD-derived kernels commonly leave rusage fields like this
//! unmaintained/zero-filled). We report involuntary as unavailable (`-1`,
//! see `mod.rs`) rather than fabricate a 0, matching the semantics already
//! used for older Linux kernels lacking `nonvoluntary_ctxt_switches`.

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use std::mem;

fn proc_task_info() -> PyResult<libc::proc_taskinfo> {
    let pid = std::process::id() as libc::c_int;
    let mut info: libc::proc_taskinfo = unsafe { mem::zeroed() };
    let size = mem::size_of::<libc::proc_taskinfo>() as libc::c_int;
    // SAFETY: `info` is a valid, appropriately-sized out-buffer for the
    // duration of the call; `pid` is our own PID so no elevated privileges
    // are required for proc_pidinfo to succeed.
    let ret = unsafe {
        libc::proc_pidinfo(
            pid,
            libc::PROC_PIDTASKINFO,
            0,
            &mut info as *mut _ as *mut _,
            size,
        )
    };
    if ret != size {
        return Err(PyOSError::new_err(format!(
            "proc_pidinfo(PROC_PIDTASKINFO) failed or returned truncated data (ret={ret})"
        )));
    }
    Ok(info)
}

// libc's mach_timebase_info is deprecated in favor of the `mach2` crate, but pulling in a whole
// new dependency for one function isn't worth it here.
#[allow(deprecated)]
fn mach_ticks_to_ns(ticks: u64) -> PyResult<u64> {
    let mut timebase: libc::mach_timebase_info = unsafe { mem::zeroed() };
    // SAFETY: `timebase` is a valid out-pointer; this call has no other preconditions.
    let ret = unsafe { libc::mach_timebase_info(&mut timebase) };
    if ret != 0 {
        return Err(PyOSError::new_err(format!(
            "mach_timebase_info failed (ret={ret})"
        )));
    }
    Ok(ticks * timebase.numer as u64 / timebase.denom as u64)
}

pub fn process_metrics() -> PyResult<(u64, u64, i64, i64, u64, u64)> {
    let info = proc_task_info()?;
    let cpu_time_user_ns = mach_ticks_to_ns(info.pti_total_user)?;
    let cpu_time_sys_ns = mach_ticks_to_ns(info.pti_total_system)?;
    let ctx_switches_voluntary = info.pti_csw as i64;
    let ctx_switches_involuntary = -1;
    let num_threads = info.pti_threadnum as u64;
    let rss_bytes = info.pti_resident_size;

    Ok((
        cpu_time_sys_ns,
        cpu_time_user_ns,
        ctx_switches_voluntary,
        ctx_switches_involuntary,
        num_threads,
        rss_bytes,
    ))
}

pub fn total_memory_bytes() -> PyResult<u64> {
    let mut mem_size: u64 = 0;
    let mut size = mem::size_of::<u64>();
    let name = c"hw.memsize";
    // SAFETY: `name` is a valid NUL-terminated C string; `mem_size`/`size`
    // are a validly sized out-buffer/length pair for sysctlbyname.
    let ret = unsafe {
        libc::sysctlbyname(
            name.as_ptr(),
            &mut mem_size as *mut _ as *mut libc::c_void,
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    if ret != 0 {
        return Err(PyOSError::new_err("sysctlbyname(hw.memsize) failed"));
    }

    let mut swap: libc::xsw_usage = unsafe { mem::zeroed() };
    let mut swap_size = mem::size_of::<libc::xsw_usage>();
    let mut mib = [libc::CTL_VM, libc::VM_SWAPUSAGE];
    // SAFETY: `mib` names a valid two-level sysctl node; `swap`/`swap_size`
    // are a validly sized out-buffer/length pair.
    let ret = unsafe {
        libc::sysctl(
            mib.as_mut_ptr(),
            mib.len() as u32,
            &mut swap as *mut _ as *mut libc::c_void,
            &mut swap_size,
            std::ptr::null_mut(),
            0,
        )
    };
    let swap_total = if ret == 0 { swap.xsu_total } else { 0 };

    Ok(mem_size + swap_total)
}
