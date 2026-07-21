use pyo3::ffi;
use pyo3::prelude::*;

/// Set a `ContextVar` while protecting against a CPython crash.
///
/// `PyContextVar_Set` rebuilds the thread state's context HAMT and is not
/// atomic: the allocations it performs while building the new trie can trigger
/// a cyclic GC pass, and it then Py_DECREF's the previous HAMT root, which can
/// start a dealloc cascade. If a node still referenced by the in-flight rebuild
/// is collected during that window, the next access to it faults with
/// `SEGV_MAPERR`.
///
/// PyContext_CopyCurrent returns a fresh context object that shares -- and
/// holds a strong reference to -- the current context's variable storage. Keeping
/// that snapshot alive across the set pins the whole storage graph as
/// GC-reachable, so neither the Py_DECREF cascade nor a concurrent GC pass can
/// free a node that is still in use. The snapshot (and the discarded token) are
/// released once the set has completed and the context is consistent again.
#[pyfunction]
pub fn safe_contextvar_set(
    py: Python<'_>,
    var: &Bound<'_, PyAny>,
    value: &Bound<'_, PyAny>,
) -> PyResult<()> {
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

/// Read a `contextvars.ContextVar` through the C-API (`PyContextVar_Get`) instead of a
/// Python-level `.get()` method dispatch — cheaper on hot read paths.
///
/// Unlike the set path there is no CPython crash to guard against here; the `unsafe` is
/// only because every `pyo3::ffi` raw C-API call is unsafe and PyO3 ships no safe
/// high-level `ContextVar` wrapper. It is sound: the GIL is held, `var` is a live
/// ContextVar, and the returned new reference is handed to a `Bound`.
///
/// Callers that pass a ContextVar created with `default=...` get that default (never a
/// null pointer) when the variable is unset; a truly-unset var with no default yields
/// Python `None`.
#[inline]
pub fn contextvar_get<'py>(
    py: Python<'py>,
    var: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    // SAFETY: the GIL is held for the whole call; `var` is a live ContextVar object.
    unsafe {
        let mut value: *mut ffi::PyObject = std::ptr::null_mut();
        if ffi::PyContextVar_Get(var.as_ptr(), std::ptr::null_mut(), &mut value) < 0 {
            return Err(PyErr::take(py).unwrap_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("PyContextVar_Get failed")
            }));
        }
        if value.is_null() {
            Ok(py.None().into_bound(py))
        } else {
            Ok(Bound::from_owned_ptr(py, value))
        }
    }
}

pub fn register_contextvar(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(safe_contextvar_set, m)?)?;
    Ok(())
}
