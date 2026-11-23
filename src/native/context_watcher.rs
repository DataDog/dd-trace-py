use pyo3::prelude::*;

#[cfg(not(Py_GIL_DISABLED))]
mod gil {
    use pyo3::prelude::*;
    use pyo3::types::context::{watch_callback, ContextEvent, ContextWatcherGuard};
    use pyo3::types::PyContext;
    use std::sync::OnceLock;

    static WATCHER: OnceLock<Option<ContextWatcherGuard>> = OnceLock::new();

    pub(super) fn register(py: Python<'_>) -> bool {
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

    pub(super) fn is_registered() -> bool {
        matches!(WATCHER.get(), Some(Some(_)))
    }

    fn context_watcher(py: Python<'_>, event: ContextEvent<'_, '_>) -> PyResult<()> {
        if matches!(event, ContextEvent::Switched(_)) {
            // CPython context watchers are reentrant. Listeners must not enter
            // another Context; the OTel listener does not do so.
            crate::event_hub::dispatch(py, "python.context.switch", None, false)?;
        }
        Ok(())
    }
}

#[pyfunction]
fn register_context_watcher(py: Python<'_>) -> bool {
    #[cfg(not(Py_GIL_DISABLED))]
    {
        gil::register(py)
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
        gil::is_registered()
    }
    #[cfg(Py_GIL_DISABLED)]
    {
        false
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(register_context_watcher, m)?)?;
    m.add_function(wrap_pyfunction!(is_context_watcher_registered, m)?)
}
