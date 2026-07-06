use libdd_shared_runtime::{SharedRuntime, SharedRuntimeError};
use pyo3::prelude::*;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

mod exceptions;
mod fork_diag;
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
        // Register OS-level fork diagnostics as soon as the runtime exists so we
        // count every subsequent fork(2), including ones that bypass CPython's
        // os.register_at_fork hooks.
        fork_diag::ensure_registered();
        Ok(Self { inner })
    }

    fn before_fork(&self) {
        self.inner.before_fork();
        // Runtime is torn down: a child forked from here has an empty slot.
        fork_diag::set_runtime_parked(true);
    }

    fn after_fork_parent(&self) -> PyResult<()> {
        let result = self
            .inner
            .after_fork_parent()
            .map_err(shared_runtime_error_to_pyerr);
        fork_diag::set_runtime_parked(false);
        result
    }

    fn after_fork_child(&self) -> PyResult<()> {
        let result = self
            .inner
            .after_fork_child()
            .map_err(shared_runtime_error_to_pyerr);
        fork_diag::set_runtime_parked(false);
        result
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

    /// Diagnostic: number of times an inherited runtime had to be abandoned in a
    /// forked child because `before_fork` was skipped for the fork. Non-zero means
    /// the pre-fork hook did not run for the fork that created this process.
    fn stale_runtimes_forgotten(&self) -> u64 {
        self.inner.stale_runtimes_forgotten()
    }

    /// Diagnostic: OS-level fork counts `(prepare, parent, child)` observed via
    /// `pthread_atfork`. These fire for every `fork(2)` in the process, so
    /// comparing `parent` against the Python-side `before_fork` count reveals
    /// forks that bypassed CPython's `os.register_at_fork` pre-fork hook.
    fn os_fork_counts(&self) -> (u64, u64, u64) {
        fork_diag::os_fork_counts()
    }

    /// Diagnostic: number of OS-level child forks that inherited a *live*
    /// (non-parked) runtime — i.e. forks that skipped `before_fork` entirely.
    fn bare_fork_live_runtime(&self) -> u64 {
        fork_diag::bare_fork_live_runtime()
    }
}

pub fn register_shared_runtime(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SharedRuntimePy>()?;
    exceptions::register_exceptions(m)?;
    Ok(())
}
