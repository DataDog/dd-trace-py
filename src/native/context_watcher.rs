use pyo3::exceptions::PyRuntimeError;
use pyo3::ffi;
use pyo3::prelude::*;
use std::ffi::{c_int, c_uint};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::OnceLock;

const CONTEXT_SWITCH_EVENT: &str = "python.context.switch";

type PyContextEvent = c_uint;
const PY_CONTEXT_SWITCHED: PyContextEvent = 1;
type PyContextWatchCallback =
    unsafe extern "C" fn(event: PyContextEvent, object: *mut ffi::PyObject) -> c_int;

unsafe extern "C" {
    fn PyContext_AddWatcher(callback: PyContextWatchCallback) -> c_int;
}

static WATCHER_ID: OnceLock<Option<c_int>> = OnceLock::new();

#[pyfunction]
pub fn register_context_watcher(py: Python<'_>) -> bool {
    WATCHER_ID
        .get_or_init(|| {
            // SAFETY: This module is only compiled for CPython 3.14+ with the
            // GIL enabled, and the callback signature matches
            // PyContext_WatchCallback from cpython/context.h.
            let watcher_id = unsafe { PyContext_AddWatcher(context_watcher) };
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
    // SAFETY: CPython invokes context watchers on the attached thread performing
    // the context switch.
    let py = unsafe { Python::assume_attached() };

    let result = catch_unwind(AssertUnwindSafe(|| {
        if event == PY_CONTEXT_SWITCHED {
            // Listeners must not enter another Context: CPython context watchers
            // are reentrant. The OTel listener does not enter a Context.
            crate::event_hub::dispatch(py, CONTEXT_SWITCH_EVENT, None, false)
        } else {
            Ok(())
        }
    }));

    let callback_result = match result {
        Ok(Ok(())) => 0,
        Ok(Err(error)) => {
            error.restore(py);
            -1
        }
        Err(_) => {
            PyRuntimeError::new_err("panic in Python context watcher").restore(py);
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
