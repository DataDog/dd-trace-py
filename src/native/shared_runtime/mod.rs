use libdd_shared_runtime::{SharedRuntime, SharedRuntimeError};
use pyo3::prelude::*;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

mod exceptions;
use exceptions::shared_runtime_error_to_pyerr;

static SHARED_RUNTIME: OnceLock<Arc<SharedRuntime>> = OnceLock::new();
static INIT_LOCK: Mutex<()> = Mutex::new(());

fn global_shared_runtime() -> Result<Arc<SharedRuntime>, SharedRuntimeError> {
    if let Some(rt) = SHARED_RUNTIME.get() {
        return Ok(rt.clone());
    }
    let _guard = INIT_LOCK
        .lock()
        .expect("shared runtime init mutex poisoned");
    if let Some(rt) = SHARED_RUNTIME.get() {
        return Ok(rt.clone());
    }
    let rt = Arc::new(SharedRuntime::new()?);
    let _ = SHARED_RUNTIME.set(rt.clone());
    Ok(rt)
}

#[pyclass(name = "SharedRuntime", subclass, frozen)]
pub struct SharedRuntimePy {
    inner: Arc<SharedRuntime>,
}

impl SharedRuntimePy {
    pub(crate) fn as_arc(&self) -> &Arc<SharedRuntime> {
        &self.inner
    }
}

#[pymethods]
impl SharedRuntimePy {
    #[new]
    fn new() -> PyResult<Self> {
        let inner = global_shared_runtime().map_err(shared_runtime_error_to_pyerr)?;
        Ok(Self { inner })
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

    fn debug(&self) -> String {
        format!("{:?}", self.inner)
    }
}

pub fn register_shared_runtime(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SharedRuntimePy>()?;
    exceptions::register_exceptions(m)?;
    Ok(())
}
