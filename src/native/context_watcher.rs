use pyo3::prelude::*;

#[cfg(not(Py_GIL_DISABLED))]
use pyo3::types::{
    context::{watch_callback, ContextEvent, ContextWatcherGuard},
    PyContext,
};
#[cfg(not(Py_GIL_DISABLED))]
use std::sync::OnceLock;

#[cfg(not(Py_GIL_DISABLED))]
static WATCHER: OnceLock<Option<ContextWatcherGuard>> = OnceLock::new();

#[pyfunction]
fn register_context_watcher(py: Python<'_>) -> bool {
    #[cfg(not(Py_GIL_DISABLED))]
    {
        WATCHER
            .get_or_init(|| {
                // Keep the watcher for the life of the process. If no slot is
                // available, cache the failure so integrations use their fallbacks.
                PyContext::add_watcher(py, watch_callback!(context_watcher))
                    .ok()
                    .map(|guard| guard.unbind())
            })
            .is_some()
    }
    #[cfg(Py_GIL_DISABLED)]
    {
        let _ = py;
        false
    }
}

#[pyfunction]
fn is_context_watcher_registered() -> bool {
    #[cfg(not(Py_GIL_DISABLED))]
    {
        matches!(WATCHER.get(), Some(Some(_)))
    }
    #[cfg(Py_GIL_DISABLED)]
    {
        false
    }
}

#[cfg(not(Py_GIL_DISABLED))]
fn context_watcher(py: Python<'_>, event: ContextEvent<'_, '_>) -> PyResult<()> {
    if matches!(event, ContextEvent::Switched(_)) {
        // CPython context watchers are reentrant. Listeners must not enter
        // another Context; the OTel listener does not do so.
        crate::event_hub::dispatch(py, "python.context.switch", None, false)?;
    }
    Ok(())
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(register_context_watcher, m)?)?;
    m.add_function(wrap_pyfunction!(is_context_watcher_registered, m)?)
}
