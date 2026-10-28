//! Windows process metrics, modelled on
//! `ddtrace/vendor/psutil/arch/windows/proc_info.c`
//! (`psutil_get_proc_info`/`psutil_proc_info`) and `_pswindows.py`.
//!
//! A single `NtQuerySystemInformation(SystemProcessInformation, ...)` call
//! yields cpu times, working-set size, thread count, and per-thread context
//! switches for every process on the system in one buffer -- we grow the
//! buffer and retry on `STATUS_BUFFER_TOO_SMALL`/`STATUS_INFO_LENGTH_MISMATCH`
//! exactly as psutil does, then walk the linked list to find our own PID.
//! `NtQuerySystemInformation` is an undocumented `ntdll.dll` export, resolved
//! via `GetProcAddress` exactly as
//! `ddtrace/vendor/psutil/arch/windows/init.c` already does -- this is not
//! new exposure, just the same dependency called from Rust instead of C.
//!
//! Only voluntary context switches are available this way (matching
//! `_pswindows.py`'s `num_ctx_switches()`, which reports involuntary as 0
//! since Windows doesn't expose that breakdown).

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use std::ffi::{c_void, CString};
use std::sync::OnceLock;

type Handle = *mut c_void;

#[repr(C)]
struct UnicodeString {
    length: u16,
    maximum_length: u16,
    buffer: *mut u16,
}

#[repr(C)]
struct SystemThreadInformation {
    kernel_time: i64,
    user_time: i64,
    create_time: i64,
    wait_time: u32,
    start_address: *mut c_void,
    unique_process: Handle,
    unique_thread: Handle,
    priority: i32,
    base_priority: i32,
    context_switches: u32,
    thread_state: u32,
    wait_reason: u32,
}

#[repr(C)]
struct SystemProcessInformation {
    next_entry_offset: u32,
    number_of_threads: u32,
    spare_li1: i64,
    spare_li2: i64,
    spare_li3: i64,
    create_time: i64,
    user_time: i64,
    kernel_time: i64,
    image_name: UnicodeString,
    base_priority: i32,
    unique_process_id: Handle,
    inherited_from_unique_process_id: Handle,
    handle_count: u32,
    session_id: u32,
    page_directory_base: usize,
    peak_virtual_size: usize,
    virtual_size: usize,
    page_fault_count: u32,
    peak_working_set_size: usize,
    working_set_size: usize,
    quota_peak_paged_pool_usage: usize,
    quota_paged_pool_usage: usize,
    quota_peak_non_paged_pool_usage: usize,
    quota_non_paged_pool_usage: usize,
    pagefile_usage: usize,
    peak_pagefile_usage: usize,
    private_page_count: usize,
    read_operation_count: i64,
    write_operation_count: i64,
    other_operation_count: i64,
    read_transfer_count: i64,
    write_transfer_count: i64,
    other_transfer_count: i64,
    // Followed by `number_of_threads` SystemThreadInformation entries, laid
    // out in-place (variable-length struct) -- see `threads()` below.
}

const SYSTEM_PROCESS_INFORMATION_CLASS: u32 = 5;
const STATUS_SUCCESS: i32 = 0x0000_0000u32 as i32;
const STATUS_BUFFER_TOO_SMALL: i32 = 0xC000_0023u32 as i32;
const STATUS_INFO_LENGTH_MISMATCH: i32 = 0xC000_0004u32 as i32;

type NtQuerySystemInformationFn = unsafe extern "system" fn(
    system_information_class: u32,
    system_information: *mut c_void,
    system_information_length: u32,
    return_length: *mut u32,
) -> i32;

#[link(name = "kernel32")]
extern "system" {
    fn GetCurrentProcessId() -> u32;
    fn LoadLibraryA(lp_lib_file_name: *const i8) -> *mut c_void;
    fn GetProcAddress(h_module: *mut c_void, lp_proc_name: *const i8) -> *mut c_void;
    fn GlobalMemoryStatusEx(lp_buffer: *mut MemoryStatusEx) -> i32;
}

#[repr(C)]
struct MemoryStatusEx {
    length: u32,
    memory_load: u32,
    total_phys: u64,
    avail_phys: u64,
    total_page_file: u64,
    avail_page_file: u64,
    total_virtual: u64,
    avail_virtual: u64,
    avail_extended_virtual: u64,
}

fn nt_query_system_information() -> PyResult<NtQuerySystemInformationFn> {
    static CACHED: OnceLock<usize> = OnceLock::new();

    let addr = *CACHED.get_or_init(|| {
        let lib_name = CString::new("ntdll.dll").expect("no interior NUL");
        let fn_name = CString::new("NtQuerySystemInformation").expect("no interior NUL");
        // SAFETY: `lib_name`/`fn_name` are valid NUL-terminated strings for the
        // duration of these calls. `ntdll.dll` is always loaded in every Windows
        // process, so LoadLibraryA here just increments its refcount.
        unsafe {
            let module = LoadLibraryA(lib_name.as_ptr() as *const i8);
            if module.is_null() {
                return 0;
            }
            GetProcAddress(module, fn_name.as_ptr() as *const i8) as usize
        }
    });

    if addr == 0 {
        return Err(PyOSError::new_err(
            "failed to resolve NtQuerySystemInformation from ntdll.dll",
        ));
    }
    // SAFETY: `addr` was returned by GetProcAddress for a symbol resolved
    // against the well-known `NtQuerySystemInformation` signature.
    Ok(unsafe { std::mem::transmute::<usize, NtQuerySystemInformationFn>(addr) })
}

/// Grow-and-retry buffer allocation matching `psutil_get_proc_info`, then walk
/// the linked list (`NextEntryOffset`) to find `pid`'s `SYSTEM_PROCESS_INFORMATION`.
/// Returns the raw buffer (offset positioned at the entry) so the caller can
/// also read the variable-length `Threads[]` array that follows it in memory.
fn query_process_info(pid: u32) -> PyResult<(Vec<u8>, usize)> {
    let nt_query_system_information = nt_query_system_information()?;

    let mut buffer_size: u32 = 0x4000;
    let mut buffer = vec![0u8; buffer_size as usize];
    let status = loop {
        let mut return_length: u32 = 0;
        // SAFETY: `buffer` has `buffer_size` valid bytes; `return_length` is a
        // valid out-pointer. NtQuerySystemInformation writes at most `buffer_size`
        // bytes and reports the required size in `return_length` on failure.
        let status = unsafe {
            nt_query_system_information(
                SYSTEM_PROCESS_INFORMATION_CLASS,
                buffer.as_mut_ptr() as *mut c_void,
                buffer_size,
                &mut return_length,
            )
        };
        if status == STATUS_BUFFER_TOO_SMALL || status == STATUS_INFO_LENGTH_MISMATCH {
            buffer_size = return_length.max(buffer_size * 2);
            buffer = vec![0u8; buffer_size as usize];
            continue;
        }
        break status;
    };

    if status != STATUS_SUCCESS {
        return Err(PyOSError::new_err(format!(
            "NtQuerySystemInformation(SystemProcessInformation) failed with status {status:#x}"
        )));
    }

    let mut offset = 0usize;
    loop {
        if offset + std::mem::size_of::<SystemProcessInformation>() > buffer.len() {
            break;
        }
        // SAFETY: `offset` was validated above to leave enough room for the
        // fixed-size header; the buffer was filled by NtQuerySystemInformation
        // with unrelated process entries laid out back-to-back this way.
        let entry = unsafe { &*(buffer.as_ptr().add(offset) as *const SystemProcessInformation) };
        if entry.unique_process_id as usize as u32 == pid {
            return Ok((buffer, offset));
        }
        if entry.next_entry_offset == 0 {
            break;
        }
        offset += entry.next_entry_offset as usize;
    }

    Err(PyOSError::new_err(
        "NtQuerySystemInformation(SystemProcessInformation): current PID not found in process list",
    ))
}

pub fn process_metrics() -> PyResult<(u64, u64, i64, i64, u64, u64)> {
    // SAFETY: GetCurrentProcessId takes no arguments and cannot fail.
    let pid = unsafe { GetCurrentProcessId() };
    let (buffer, offset) = query_process_info(pid)?;
    // SAFETY: `offset` was validated by `query_process_info` to point at a
    // complete `SystemProcessInformation` header within `buffer`.
    let entry = unsafe { &*(buffer.as_ptr().add(offset) as *const SystemProcessInformation) };

    let cpu_time_user_ns = (entry.user_time as u64).saturating_mul(100);
    let cpu_time_sys_ns = (entry.kernel_time as u64).saturating_mul(100);
    let num_threads = entry.number_of_threads as u64;
    let rss_bytes = entry.working_set_size as u64;

    let threads_offset = offset + std::mem::size_of::<SystemProcessInformation>();
    let mut ctx_switches_voluntary: u64 = 0;
    for i in 0..entry.number_of_threads as usize {
        let thread_offset = threads_offset + i * std::mem::size_of::<SystemThreadInformation>();
        if thread_offset + std::mem::size_of::<SystemThreadInformation>() > buffer.len() {
            break;
        }
        // SAFETY: bounds-checked above; the Threads[] array is laid out
        // in-place immediately after the SystemProcessInformation header,
        // exactly as psutil's PSUTIL_FIRST_PROCESS/Threads[] access relies on.
        let thread =
            unsafe { &*(buffer.as_ptr().add(thread_offset) as *const SystemThreadInformation) };
        ctx_switches_voluntary += thread.context_switches as u64;
    }

    Ok((
        cpu_time_sys_ns,
        cpu_time_user_ns,
        ctx_switches_voluntary as i64,
        // Windows doesn't expose a voluntary/involuntary breakdown; psutil's
        // own _pswindows.py reports 0 here rather than "unavailable".
        0,
        num_threads,
        rss_bytes,
    ))
}

pub fn total_memory_bytes() -> PyResult<u64> {
    let mut status = MemoryStatusEx {
        length: std::mem::size_of::<MemoryStatusEx>() as u32,
        memory_load: 0,
        total_phys: 0,
        avail_phys: 0,
        total_page_file: 0,
        avail_page_file: 0,
        total_virtual: 0,
        avail_virtual: 0,
        avail_extended_virtual: 0,
    };
    // SAFETY: `status.length` is set to sizeof(MemoryStatusEx) as required by
    // GlobalMemoryStatusEx; `status` is a valid out-buffer for the call.
    let ok = unsafe { GlobalMemoryStatusEx(&mut status) };
    if ok == 0 {
        return Err(PyOSError::new_err("GlobalMemoryStatusEx failed"));
    }
    // total_page_file already includes total_phys on Windows (it's the
    // commit limit, backed by RAM + the pagefile), so this is the same
    // "physical + swap" quantity psutil computes from
    // `virtual_memory().total + swap_memory().total`.
    Ok(status.total_page_file)
}
