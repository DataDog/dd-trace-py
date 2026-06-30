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

pub fn register_contextvar(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(safe_contextvar_set, m)?)?;
    Ok(())
}
