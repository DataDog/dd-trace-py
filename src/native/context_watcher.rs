use pyo3::exceptions::PyRuntimeError;
use pyo3::ffi;
use pyo3::prelude::*;
use std::ffi::{c_int, c_uint};
use std::panic::{catch_unwind, AssertUnwindSafe};

const CONTEXT_SWITCH_EVENT: &str = "python.context.switch";

type PyContextEvent = c_uint;
const PY_CONTEXT_SWITCHED: PyContextEvent = 1;
type PyContextWatchCallback =
    unsafe extern "C" fn(event: PyContextEvent, object: *mut ffi::PyObject) -> c_int;

unsafe extern "C" {
    fn PyContext_AddWatcher(callback: PyContextWatchCallback) -> c_int;
}

pub fn register(py: Python<'_>) -> PyResult<()> {
    // SAFETY: This module is only compiled for CPython 3.14+, and the callback
    // signature matches PyContext_WatchCallback from cpython/context.h.
    let watcher_id = unsafe { PyContext_AddWatcher(context_watcher) };
    if watcher_id == -1 {
        return Err(PyErr::fetch(py));
    }

    Ok(())
}

unsafe extern "C" fn context_watcher(event: PyContextEvent, object: *mut ffi::PyObject) -> c_int {
    // CPython may invoke watcher callbacks with an exception already set. Clear
    // it temporarily so listeners can use regular Python APIs, then restore it.
    let pending_exception = unsafe { ffi::PyErr_GetRaisedException() };
    // SAFETY: CPython invokes context watchers on the attached thread performing
    // the context switch.
    let py = unsafe { Python::assume_attached() };

    let result = catch_unwind(AssertUnwindSafe(|| {
        if event == PY_CONTEXT_SWITCHED {
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
