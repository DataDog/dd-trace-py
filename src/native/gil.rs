//! Finalization-safe GIL release for native worker threads.
//!
//! dd-trace-py runs blocking work (trace/telemetry/remote-config sends) on native
//! background threads that release the GIL and re-acquire it when the work returns.
//! If the interpreter starts finalizing while the GIL is released, CPython's take_gil
//! forces the (non-main) thread out via PyThread_exit_thread -> pthread_exit. That
//! forced unwind runs without the GIL and cannot cross our extern "C"/noexcept frames,
//! so the process aborts with SIGABRT.
//!
//! CPython 3.14 fixes this by hanging such threads instead of exiting them
//! (gh-87135 / python/cpython#105805). We replicate that here for older runtimes:
//! after the blocking call, a non-main thread that finds the interpreter finalizing
//! hangs instead of re-acquiring the GIL. The finalizing thread itself (approximated
//! by the main thread) re-acquires normally, so shutdown flushes still complete.
//!
//! After fork, only the calling thread exists in the child, and CPython treats that
//! thread as the main thread. A child handler re-records its ident. Without that,
//! a child whose fork caller was not the parent main thread would park the thread
//! that must finish finalization, and the process would never exit.

use pyo3::marker::Ungil;
use pyo3::{ffi, Python};
use std::os::raw::c_ulong;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

// CPython < 3.13 has no public Py_IsFinalizing(); use the private symbol, which
// returns nonzero once finalization has started. It is removed on 3.13+.
#[cfg(not(Py_3_13))]
extern "C" {
    fn _Py_IsFinalizing() -> std::os::raw::c_int;
}

extern "C" {
    // Public, stable, and callable without the GIL: the calling OS thread's Python
    // identifier. Not exposed by pyo3-ffi, so we declare it ourselves.
    // TODO: remove once pyo3-ffi exposes PyThread_get_thread_ident
    fn PyThread_get_thread_ident() -> c_ulong;
}

static MAIN_THREAD_IDENT: AtomicU64 = AtomicU64::new(0);
static MAIN_THREAD_SET: AtomicBool = AtomicBool::new(false);

fn store_current_as_main_thread() {
    MAIN_THREAD_IDENT.store(current_thread_ident(), Ordering::Relaxed);
    MAIN_THREAD_SET.store(true, Ordering::Release);
}

// Runs in the forked child, on the only surviving thread.
// Atomic store and PyThread_get_thread_ident is async-signal-safe.
#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" fn after_fork_child() {
    store_current_as_main_thread();
}

/// Record the interpreter's main thread. Call once from the module init function,
/// which runs on the importing thread (normally the main thread) with the GIL held.
/// Also registers a fork child handler so the surviving thread stays the main thread.
pub(crate) fn record_main_thread() {
    store_current_as_main_thread();
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    unsafe {
        // libc fork, including os.fork and native forks such as uWSGI, runs this
        // handler. A failed registration leaves the parent ident in place.
        let _ = libc::pthread_atfork(None, None, Some(after_fork_child));
    }
}

fn current_thread_ident() -> u64 {
    unsafe { PyThread_get_thread_ident() as u64 }
}

fn interpreter_is_finalizing() -> bool {
    #[cfg(Py_3_13)]
    {
        unsafe { ffi::Py_IsFinalizing() != 0 }
    }
    #[cfg(not(Py_3_13))]
    {
        unsafe { _Py_IsFinalizing() != 0 }
    }
}

/// Whether re-acquiring the GIL on this thread would be fatal: the interpreter is
/// finalizing and this is not the finalizing thread (approximated by the recorded
/// main thread). Only non-main threads are forced out by take_gil during
/// finalization; the finalizing thread can re-acquire safely.
fn must_hang_instead_of_reattach() -> bool {
    if !interpreter_is_finalizing() {
        return false;
    }
    // If the main thread was never recorded, do not hang: the worst case is the
    // pre-existing abort, whereas hanging the finalizing thread would deadlock exit.
    if !MAIN_THREAD_SET.load(Ordering::Acquire) {
        return false;
    }
    current_thread_ident() != MAIN_THREAD_IDENT.load(Ordering::Relaxed)
}

/// Run `f` with the GIL released, like [`Python::detach`], but if the interpreter
/// starts finalizing while the GIL is released, hang this (non-main) thread instead
/// of re-acquiring the GIL. See the module docs for why.
pub(crate) fn detach_or_hang_on_finalize<T, F>(py: Python<'_>, f: F) -> T
where
    F: Ungil + FnOnce() -> T,
    T: Ungil,
{
    let _ = py; // The GIL is held on entry, which PyEval_SaveThread requires.
    let save = unsafe { ffi::PyEval_SaveThread() };

    // Guard against a panic in f so we never unwind across the ffi boundary before
    // restoring (or deliberately not restoring) the thread state.
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));

    if must_hang_instead_of_reattach() {
        // Do not re-acquire the GIL: take_gil would exit this thread via pthread_exit
        // and abort. Park until the process exits (matches CPython 3.14 gh-87135).
        loop {
            std::thread::park();
        }
    }

    unsafe { ffi::PyEval_RestoreThread(save) };

    match result {
        Ok(value) => value,
        Err(payload) => std::panic::resume_unwind(payload),
    }
}
