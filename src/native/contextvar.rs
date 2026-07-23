use pyo3::ffi;
use pyo3::prelude::*;
use std::ffi::CString;

/// Create a new `contextvars.ContextVar` via the C API (`PyContextVar_New`)
/// with `default` as its default value. Avoids importing the `contextvars`
/// module from Rust.
pub fn contextvar_new<'py>(
    py: Python<'py>,
    name: &str,
    default: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let c_name = CString::new(name).expect("contextvar name must not contain NUL bytes");
    // SAFETY: `c_name` is a valid NUL-terminated string for the duration of the call;
    // `default.as_ptr()` is a valid borrowed reference (PyContextVar_New takes its own ref).
    unsafe {
        Bound::from_owned_ptr_or_err(py, ffi::PyContextVar_New(c_name.as_ptr(), default.as_ptr()))
    }
}

/// Read the current value of `var` via `PyContextVar_Get`.
///
/// `var` must have been created with a default (e.g. via `contextvar_new`), so this
/// never raises `LookupError` -- the default is always returned when unset.
pub fn contextvar_get<'py>(
    py: Python<'py>,
    var: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let mut value: *mut ffi::PyObject = std::ptr::null_mut();
    // SAFETY: `var` is a valid ContextVar object; `value` is a valid out-pointer.
    let rc = unsafe { ffi::PyContextVar_Get(var.as_ptr(), std::ptr::null_mut(), &mut value) };
    if rc < 0 {
        return Err(PyErr::take(py).unwrap_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("PyContextVar_Get failed")
        }));
    }
    // rc == 0 is success: `value` is a new owned reference, or NULL when the var
    // is unset and has no default. Surface NULL as Python `None` rather than
    // treating it as an error (`from_owned_ptr_or_err` would fabricate a bogus
    // exception from an empty error state).
    // SAFETY: on success `value` is either a valid new reference or NULL.
    Ok(unsafe { Bound::from_owned_ptr_or_opt(py, value) }
        .unwrap_or_else(|| py.None().into_bound(py)))
}

/// Set a `ContextVar`, protecting against a CPython crash on affected versions.
///
/// `PyContextVar_Set` is not atomic before CPython 3.12: an allocation during
/// the HAMT rebuild can trigger a cyclic GC pass that frees a node still in use,
/// crashing with SEGV_MAPERR. On affected versions we route the set through a
/// native helper that secures the context's storage during the call.
/// CPython 3.12+ is unaffected, so use the plain (and faster) set there.
///
/// This extension is built per-Python-version (no `abi3`), so the CPython
/// version is known at compile time -- gate on `Py_3_12` instead of checking
/// `py.version_info()` on every call.
#[cfg(not(Py_3_12))]
#[pyfunction]
pub fn safe_contextvar_set(
    py: Python<'_>,
    var: &Bound<'_, PyAny>,
    value: &Bound<'_, PyAny>,
) -> PyResult<()> {
    // `PyContext_CopyCurrent` returns a fresh context object that shares -- and
    // holds a strong reference to -- the current context's variable storage. Keeping
    // that snapshot alive across the set pins the whole storage graph as
    // GC-reachable, so neither the Py_DECREF cascade nor a concurrent GC pass can
    // free a node that is still in use. The snapshot (and the discarded token) are
    // released once the set has completed and the context is consistent again.
    //
    // SAFETY: the GIL (`py`) is held for every call below and is never released,
    // so the thread state and its context cannot change underneath us.
    unsafe {
        let snapshot = ffi::PyContext_CopyCurrent();
        if snapshot.is_null() {
            // Snapshotting the current context failed (e.g. out of memory); an
            // exception is already set. Propagate it rather than crashing.
            return Err(PyErr::take(py).unwrap_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("PyContext_CopyCurrent failed")
            }));
        }

        let token = ffi::PyContextVar_Set(var.as_ptr(), value.as_ptr());

        // Capture any error before the decrefs below can perturb interpreter state.
        let err = if token.is_null() {
            PyErr::take(py)
        } else {
            None
        };

        // The returned token is never used (we don't support reset here), so drop
        // it. Then release the snapshot now that the context is consistent again.
        ffi::Py_XDECREF(token);
        ffi::Py_DECREF(snapshot);

        if let Some(err) = err {
            return Err(err);
        }
    }
    Ok(())
}

/// CPython 3.12+ made `PyContextVar_Set` atomic, so no snapshot is needed here --
/// just call it directly. See the `not(Py_3_12)` overload above for why older
/// versions need the snapshot dance.
#[cfg(Py_3_12)]
#[pyfunction]
pub fn safe_contextvar_set(
    py: Python<'_>,
    var: &Bound<'_, PyAny>,
    value: &Bound<'_, PyAny>,
) -> PyResult<()> {
    // SAFETY: the GIL (`py`) is held for the duration of the call.
    unsafe {
        let token = ffi::PyContextVar_Set(var.as_ptr(), value.as_ptr());
        if token.is_null() {
            return Err(PyErr::take(py).unwrap_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("PyContextVar_Set failed")
            }));
        }
        ffi::Py_DECREF(token);
    }
    Ok(())
}

pub fn register_contextvar(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(safe_contextvar_set, m)?)?;
    Ok(())
}
