use libdd_shared_runtime::{ForkSafeRuntime, SharedRuntime};
use pyo3::prelude::*;
use std::sync::Arc;
use std::thread;
use std::time::Duration;

mod exceptions;
use exceptions::shared_runtime_error_to_pyerr;

#[pyclass(name = "SharedRuntime", subclass)]
pub struct SharedRuntimePy {
    inner: Arc<ForkSafeRuntime>,
}

impl SharedRuntimePy {
    pub(crate) fn as_arc(&self) -> &Arc<ForkSafeRuntime> {
        &self.inner
    }
}

#[pymethods]
impl SharedRuntimePy {
    #[new]
    fn new() -> PyResult<Self> {
        let inner = ForkSafeRuntime::new().map_err(shared_runtime_error_to_pyerr)?;
        Ok(Self {
            inner: Arc::new(inner),
        })
    }

    fn before_fork(&self) {
        self.inner.before_fork();
    }

    fn after_fork_parent(&self) -> PyResult<()> {
        self.inner
            .after_fork_parent()
            .map_err(shared_runtime_error_to_pyerr)
    }

    fn after_fork_child(&self) -> PyResult<()> {
        self.inner
            .after_fork_child()
            .map_err(shared_runtime_error_to_pyerr)
    }

    fn shutdown(&self, timeout_ms: Option<u64>) -> PyResult<()> {
        let timeout = timeout_ms.map(Duration::from_millis);
        self.inner
            .clone()
            .shutdown(timeout)
            .map_err(shared_runtime_error_to_pyerr)
    }

    /// Shutdown the runtime in a new thread.
    /// This is can be used when thread local storage have been destroyed.
    fn shutdown_in_thread(&self, timeout_ms: Option<u64>) -> PyResult<()> {
        let timeout = timeout_ms.map(Duration::from_millis);
        let inner = self.inner.clone();
        let result = thread::spawn(move || inner.shutdown(timeout))
            .join()
            .map_err(|_| {
                pyo3::exceptions::PyRuntimeError::new_err("Failed to join shutdown thread")
            })?;
        result.map_err(shared_runtime_error_to_pyerr)
    }

    fn debug(&self) -> String {
        format!("{:?}", self.inner)
    }
}

pub fn register_shared_runtime(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SharedRuntimePy>()?;
    exceptions::register_exceptions(m)?;
    Ok(())
}
