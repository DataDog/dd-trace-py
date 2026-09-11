use libdd_shared_runtime::{ForkSafeRuntime, SharedRuntime};
#[cfg(any(target_os = "linux", target_os = "macos"))]
use once_cell::sync::OnceCell;
#[cfg(any(target_os = "linux", target_os = "macos"))]
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::sync::atomic::AtomicBool;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, RwLock};
use std::thread;
use std::time::Duration;

mod exceptions;
use exceptions::shared_runtime_error_to_pyerr;

#[cfg(any(target_os = "linux", target_os = "macos"))]
static ATFORK_RUNTIME: OnceCell<Arc<SharedRuntimeState>> = OnceCell::new();
#[cfg(any(target_os = "linux", target_os = "macos"))]
static CHILD_RESTART_PENDING: AtomicBool = AtomicBool::new(false);
#[cfg(any(target_os = "linux", target_os = "macos"))]
static CHILD_RESTART_DEFERRED: AtomicBool = AtomicBool::new(false);
#[cfg(any(target_os = "linux", target_os = "macos"))]
static CHILD_RESTART_IN_PROGRESS: AtomicBool = AtomicBool::new(false);

struct SharedRuntimeState {
    runtime: RwLock<Arc<ForkSafeRuntime>>,
    pid: AtomicU32,
}

impl SharedRuntimeState {
    fn current(&self) -> Arc<ForkSafeRuntime> {
        self.runtime
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn atfork_runtime() -> Option<&'static Arc<SharedRuntimeState>> {
    ATFORK_RUNTIME.get()
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" fn before_fork() {
    CHILD_RESTART_DEFERRED.store(true, Ordering::Release);
    while CHILD_RESTART_IN_PROGRESS.load(Ordering::Acquire) {
        std::thread::yield_now();
    }
    if let Some(state) = atfork_runtime() {
        let runtime = state.current();
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            runtime.before_fork();
        }));
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" fn after_fork_parent() {
    if let Some(state) = atfork_runtime() {
        let runtime = state.current();
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _ = runtime.after_fork_parent();
        }));
    }
    CHILD_RESTART_DEFERRED.store(false, Ordering::Release);
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
unsafe extern "C" fn after_fork_child() {
    // Do not rebuild Tokio here. This callback also runs in fork+exec children,
    // where exec closes the new runtime's descriptors while its worker thread is using them.
    // The next Python-facing runtime or telemetry operation performs the restart instead.
    CHILD_RESTART_IN_PROGRESS.store(false, Ordering::Release);
    CHILD_RESTART_PENDING.store(true, Ordering::Release);
    CHILD_RESTART_DEFERRED.store(false, Ordering::Release);
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
struct ChildRestartGuard;

#[cfg(any(target_os = "linux", target_os = "macos"))]
impl Drop for ChildRestartGuard {
    fn drop(&mut self) {
        CHILD_RESTART_IN_PROGRESS.store(false, Ordering::Release);
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn acquire_child_restart() -> PyResult<Option<ChildRestartGuard>> {
    loop {
        if !CHILD_RESTART_PENDING.load(Ordering::Acquire) {
            return Ok(None);
        }
        if CHILD_RESTART_DEFERRED.load(Ordering::Acquire) {
            return Err(PyRuntimeError::new_err(
                "native runtime restart is deferred until child fork hooks complete",
            ));
        }
        if CHILD_RESTART_IN_PROGRESS
            .compare_exchange_weak(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
        {
            let guard = ChildRestartGuard;
            if !CHILD_RESTART_PENDING.load(Ordering::Acquire) {
                return Ok(None);
            }
            if CHILD_RESTART_DEFERRED.load(Ordering::Acquire) {
                return Err(PyRuntimeError::new_err(
                    "native runtime restart is deferred until child fork hooks complete",
                ));
            }
            return Ok(Some(guard));
        }
        std::thread::yield_now();
    }
}

#[pyclass(name = "SharedRuntime", subclass)]
pub struct SharedRuntimePy {
    inner: Arc<SharedRuntimeState>,
}

impl SharedRuntimePy {
    pub(crate) fn as_arc(&self) -> PyResult<Arc<ForkSafeRuntime>> {
        ensure_shared_runtime_after_fork(&self.inner)
    }
}

pub(crate) fn ensure_after_fork_child(runtime: &Arc<ForkSafeRuntime>) -> PyResult<()> {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        // Telemetry calls this for every metric point. Keep the normal path to one
        // read-only load; an unconditional swap and PID check caused a substantial hot-path
        // regression even when the process had never forked.
        if !CHILD_RESTART_PENDING.load(Ordering::Acquire) {
            return Ok(());
        }
        if let Some(_restart_guard) = acquire_child_restart()? {
            if let Err(e) = runtime.after_fork_child() {
                return Err(shared_runtime_error_to_pyerr(e));
            }
            if let Some(state) = atfork_runtime() {
                state.pid.store(std::process::id(), Ordering::Release);
            }
            CHILD_RESTART_PENDING.store(false, Ordering::Release);
        } else if let Some(state) = atfork_runtime() {
            if state.pid.load(Ordering::Acquire) != std::process::id() {
                return Err(PyRuntimeError::new_err(
                    "native worker was inherited by a child without native fork handlers; rebuild the worker",
                ));
            }
        }
    }
    Ok(())
}

fn ensure_shared_runtime_after_fork(
    state: &Arc<SharedRuntimeState>,
) -> PyResult<Arc<ForkSafeRuntime>> {
    let current_pid = std::process::id();
    if state.pid.load(Ordering::Acquire) == current_pid {
        return Ok(state.current());
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    let restart_guard = acquire_child_restart()?;

    let mut runtime = state.runtime.write().unwrap_or_else(|e| e.into_inner());
    if state.pid.load(Ordering::Acquire) == current_pid {
        return Ok(runtime.clone());
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    if restart_guard.is_some() {
        if let Err(e) = runtime.after_fork_child() {
            return Err(shared_runtime_error_to_pyerr(e));
        }
        state.pid.store(current_pid, Ordering::Release);
        CHILD_RESTART_PENDING.store(false, Ordering::Release);
        return Ok(runtime.clone());
    }

    // A Python-managed fork can run without invoking pthread_atfork on some runtimes.
    // The inherited Tokio runtime cannot be repaired because its worker threads are gone,
    // so replace it without polling or shutting it down.
    *runtime = Arc::new(ForkSafeRuntime::new().map_err(shared_runtime_error_to_pyerr)?);
    state.pid.store(current_pid, Ordering::Release);
    Ok(runtime.clone())
}

#[pymethods]
impl SharedRuntimePy {
    #[new]
    fn new() -> PyResult<Self> {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        {
            let result = ATFORK_RUNTIME.get_or_try_init(|| {
                let runtime = ForkSafeRuntime::new().map_err(|e| e.to_string())?;
                let state = Arc::new(SharedRuntimeState {
                    runtime: RwLock::new(Arc::new(runtime)),
                    pid: AtomicU32::new(std::process::id()),
                });
                // uWSGI forks in native code and bypasses Python's os.register_at_fork
                // callbacks. pthread_atfork pauses the runtime around every native fork. The child
                // callback only marks the runtime for lazy restart because it also runs in transient
                // fork+exec children, where starting threads would race with exec closing descriptors.
                // Failed hook registration leaves this cell uninitialized so a later call can retry.
                let result = unsafe {
                    libc::pthread_atfork(
                        Some(before_fork),
                        Some(after_fork_parent),
                        Some(after_fork_child),
                    )
                };
                if result != 0 {
                    return Err(format!(
                        "failed to register native fork handlers: error {result}"
                    ));
                }
                Ok(state)
            });
            match result {
                Ok(state) => Ok(Self {
                    inner: state.clone(),
                }),
                Err(error) => Err(PyRuntimeError::new_err(error)),
            }
        }

        #[cfg(not(any(target_os = "linux", target_os = "macos")))]
        let runtime = ForkSafeRuntime::new().map_err(shared_runtime_error_to_pyerr)?;
        #[cfg(not(any(target_os = "linux", target_os = "macos")))]
        Ok(Self {
            inner: Arc::new(SharedRuntimeState {
                runtime: RwLock::new(Arc::new(runtime)),
                pid: AtomicU32::new(std::process::id()),
            }),
        })
    }

    fn before_fork(&self) {
        self.inner.current().before_fork();
    }

    fn after_fork_parent(&self) -> PyResult<()> {
        self.inner
            .current()
            .after_fork_parent()
            .map_err(shared_runtime_error_to_pyerr)
    }

    fn after_fork_child(&self) -> PyResult<()> {
        let runtime = self.inner.current();
        let result = runtime
            .after_fork_child()
            .map_err(shared_runtime_error_to_pyerr);
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        if result.is_ok() {
            CHILD_RESTART_PENDING.store(false, Ordering::Release);
            self.inner.pid.store(std::process::id(), Ordering::Release);
        }
        result
    }

    fn defer_after_fork_child(&self) {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        {
            // Python fork hooks can run without pthread_atfork on some runtimes. Mark the restart
            // pending here too so ensure_after_fork_child can keep its no-fork fast path.
            CHILD_RESTART_DEFERRED.store(true, Ordering::Release);
            CHILD_RESTART_PENDING.store(true, Ordering::Release);
        }
    }

    fn allow_after_fork_child(&self) {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        CHILD_RESTART_DEFERRED.store(false, Ordering::Release);
    }

    fn register_at_fork(&self) -> PyResult<()> {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        {
            match ATFORK_RUNTIME.get() {
                Some(state) if Arc::ptr_eq(state, &self.inner) => Ok(()),
                Some(_) => Err(PyRuntimeError::new_err(
                    "native fork handlers are already registered to another shared runtime",
                )),
                None => Err(PyRuntimeError::new_err(
                    "native fork handlers were not registered during runtime creation",
                )),
            }
        }
        #[cfg(not(any(target_os = "linux", target_os = "macos")))]
        Ok(())
    }

    fn shutdown(&self, timeout_ms: Option<u64>) -> PyResult<()> {
        let timeout = timeout_ms.map(Duration::from_millis);
        self.inner
            .current()
            .shutdown(timeout)
            .map_err(shared_runtime_error_to_pyerr)
    }

    /// Shutdown the runtime in a new thread.
    /// This is can be used when thread local storage have been destroyed.
    fn shutdown_in_thread(&self, timeout_ms: Option<u64>) -> PyResult<()> {
        let timeout = timeout_ms.map(Duration::from_millis);
        let inner = self.inner.current();
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
        format!("{:?}", self.inner.current())
    }
}

pub fn register_shared_runtime(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SharedRuntimePy>()?;
    exceptions::register_exceptions(m)?;
    Ok(())
}
