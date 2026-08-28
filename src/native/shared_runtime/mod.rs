use libdd_shared_runtime::{ForkSafeRuntime, SharedRuntime};
#[cfg(any(target_os = "linux", target_os = "macos"))]
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::sync::OnceLock;
use std::thread;
use std::time::Duration;

mod exceptions;
use exceptions::shared_runtime_error_to_pyerr;

#[cfg(any(target_os = "linux", target_os = "macos"))]
static ATFORK_RUNTIME: OnceLock<Arc<ForkSafeRuntime>> = OnceLock::new();
#[cfg(any(target_os = "linux", target_os = "macos"))]
static CHILD_RESTART_PENDING: AtomicBool = AtomicBool::new(false);

#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" fn before_fork() {
    if let Some(runtime) = ATFORK_RUNTIME.get() {
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            runtime.before_fork();
        }));
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" fn after_fork_parent() {
    if let Some(runtime) = ATFORK_RUNTIME.get() {
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _ = runtime.after_fork_parent();
        }));
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" fn after_fork_child() {
    // AIDEV-NOTE: Do not rebuild Tokio here. This callback also runs in fork+exec children,
    // where exec closes the new runtime's descriptors while its worker thread is using them.
    // The next Python-facing runtime or telemetry operation performs the restart instead.
    CHILD_RESTART_PENDING.store(true, Ordering::Release);
}

#[pyclass(name = "SharedRuntime", subclass)]
pub struct SharedRuntimePy {
    inner: Arc<ForkSafeRuntime>,
}

impl SharedRuntimePy {
    pub(crate) fn as_arc(&self) -> PyResult<&Arc<ForkSafeRuntime>> {
        ensure_after_fork_child(&self.inner)?;
        Ok(&self.inner)
    }
}

pub(crate) fn ensure_after_fork_child(runtime: &Arc<ForkSafeRuntime>) -> PyResult<()> {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        if CHILD_RESTART_PENDING.load(Ordering::Acquire) {
            runtime
                .after_fork_child()
                .map_err(shared_runtime_error_to_pyerr)?;
            CHILD_RESTART_PENDING.store(false, Ordering::Release);
        }
    }
    Ok(())
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
        let result = self
            .inner
            .after_fork_child()
            .map_err(shared_runtime_error_to_pyerr);
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        if result.is_ok() {
            CHILD_RESTART_PENDING.store(false, Ordering::Release);
        }
        result
    }

    fn register_at_fork(&self) -> PyResult<()> {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        {
            if let Some(runtime) = ATFORK_RUNTIME.get() {
                if Arc::ptr_eq(runtime, &self.inner) {
                    return Ok(());
                }
                return Err(PyRuntimeError::new_err(
                    "native fork handlers are already registered to another shared runtime",
                ));
            }

            // AIDEV-NOTE: uWSGI forks in native code and bypasses Python's os.register_at_fork
            // callbacks. pthread_atfork pauses the runtime around every native fork. The child
            // callback only marks the runtime for lazy restart because it also runs in transient
            // fork+exec children, where starting threads would race with exec closing descriptors.
            ATFORK_RUNTIME.set(self.inner.clone()).map_err(|_| {
                PyRuntimeError::new_err(
                    "failed to register shared runtime for native fork handlers",
                )
            })?;
            let result = unsafe {
                libc::pthread_atfork(
                    Some(before_fork),
                    Some(after_fork_parent),
                    Some(after_fork_child),
                )
            };
            if result != 0 {
                return Err(PyRuntimeError::new_err(format!(
                    "failed to register native fork handlers: error {result}"
                )));
            }
        }
        Ok(())
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
        thread::Builder::new()
            .spawn(move || inner.shutdown(timeout))
            .map_err(|_| {
                pyo3::exceptions::PyRuntimeError::new_err("Failed to start shutdown thread")
            })?
            .join()
            .map_err(|_| {
                pyo3::exceptions::PyRuntimeError::new_err("Failed to join shutdown thread")
            })?
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
