use pyo3::ffi;
use pyo3::prelude::*;
use std::ffi::{c_int, c_uint};
use std::matches;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::OnceLock;

#[cfg(windows)]
use windows_sys::Win32::System::LibraryLoader::{
    GetModuleHandleExW, GetProcAddress, GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
    GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
};

const CONTEXT_SWITCH_EVENT: &str = "python.context.switch";

type PyContextEvent = c_uint;
const PY_CONTEXT_SWITCHED: PyContextEvent = 1;
type PyContextWatchCallback =
    unsafe extern "C" fn(event: PyContextEvent, object: *mut ffi::PyObject) -> c_int;

#[cfg(not(windows))]
unsafe extern "C" {
    fn PyContext_AddWatcher(callback: PyContextWatchCallback) -> c_int;
}

#[cfg(not(windows))]
unsafe fn py_context_add_watcher(callback: PyContextWatchCallback) -> Option<c_int> {
    Some(unsafe { PyContext_AddWatcher(callback) })
}

#[cfg(windows)]
unsafe fn py_context_add_watcher(callback: PyContextWatchCallback) -> Option<c_int> {
    let mut python_module = std::ptr::null_mut();
    let flags =
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT;
    let python_symbol = ffi::PyContextVar_New as *const () as *const u16;
    if unsafe { GetModuleHandleExW(flags, python_symbol, &mut python_module) } == 0 {
        return None;
    }

    // PyO3 does not yet declare the 3.14 context-watcher API, so resolve it
    // from the same Python DLL as its existing contextvars imports.
    let address =
        unsafe { GetProcAddress(python_module, c"PyContext_AddWatcher".as_ptr().cast()) }?;
    let add_watcher: unsafe extern "C" fn(PyContextWatchCallback) -> c_int =
        unsafe { std::mem::transmute(address) };
    Some(unsafe { add_watcher(callback) })
}

static WATCHER_ID: OnceLock<Option<c_int>> = OnceLock::new();

#[pyfunction]
pub fn register_context_watcher(py: Python<'_>) -> bool {
    WATCHER_ID
        .get_or_init(|| {
            // SAFETY: This module is only compiled for CPython 3.14+ with the
            // GIL enabled, and the callback signature matches
            // PyContext_WatchCallback from cpython/context.h.
            let watcher_id = unsafe { py_context_add_watcher(context_watcher) }?;
            if watcher_id == -1 {
                // Context-switch publication is optional. If no watcher slot
                // is available, clear the C-API error and leave it disabled.
                drop(PyErr::fetch(py));
                None
            } else {
                Some(watcher_id)
            }
        })
        .is_some()
}

#[pyfunction]
pub fn is_context_watcher_registered() -> bool {
    matches!(WATCHER_ID.get(), Some(Some(_)))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(register_context_watcher, m)?)?;
    m.add_function(wrap_pyfunction!(is_context_watcher_registered, m)?)
}

unsafe extern "C" fn context_watcher(event: PyContextEvent, object: *mut ffi::PyObject) -> c_int {
    // CPython may invoke watcher callbacks with an exception already set. Clear
    // it temporarily so listeners can use regular Python APIs, then restore it;
    // losing it makes Context.run raise SystemError instead of the original error.
    let pending_exception = unsafe { ffi::PyErr_GetRaisedException() };
    let callback_result = match catch_unwind(AssertUnwindSafe(|| {
        // CPython invokes context watchers on an attached thread, but entering
        // through the C API bypasses PyO3's attachment bookkeeping.
        Python::attach(|py| {
            if event == PY_CONTEXT_SWITCHED {
                // Listeners must not enter another Context: CPython context watchers
                // are reentrant. The OTel listener does not enter a Context.
                if let Err(error) =
                    crate::event_hub::dispatch(py, CONTEXT_SWITCH_EVENT, None, false)
                {
                    error.restore(py);
                    return -1;
                }
            }

            0
        })
    })) {
        Ok(result) => result,
        Err(_) => {
            // Keep panics from crossing the C boundary even if attaching itself
            // fails before a Python token is available.
            unsafe {
                ffi::PyErr_SetString(
                    ffi::PyExc_RuntimeError,
                    c"panic in Python context watcher".as_ptr(),
                )
            };
            -1
        }
    };

    if pending_exception.is_null() {
        return callback_result;
    }

    // A new callback error must not replace the exception which was pending on
    // entry. Report it as unraisable before restoring the original exception.
    unsafe {
        if callback_result == -1 {
            ffi::PyErr_WriteUnraisable(object);
        }
        ffi::PyErr_Clear();
        ffi::PyErr_SetRaisedException(pending_exception);
    }

    0
}
