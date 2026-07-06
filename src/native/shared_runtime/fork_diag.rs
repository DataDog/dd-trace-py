//! OS-level fork diagnostics for the shared runtime fork investigation.
//!
//! CPython's `os.register_at_fork` hooks (which drive `SharedRuntime.before_fork`
//! / `after_fork_child`) only run when a fork goes through the interpreter's fork
//! machinery. A native/libc `fork(2)` issued from a C extension bypasses them
//! entirely, so the child silently inherits a *live* tokio runtime while none of
//! the Python-side counters move.
//!
//! To observe that case we register `pthread_atfork` handlers, which the C
//! library invokes for *every* `fork(2)` in the process regardless of who called
//! it. Comparing these OS-level counts against the Python-side `before_fork`
//! count reveals forks that skipped the ddtrace pre-fork hook.
//!
//! The handlers run inside `fork(2)` and must be async-signal-safe: they only
//! perform relaxed atomic arithmetic — no allocation, locks, or I/O.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Once;

/// Number of times each `pthread_atfork` phase fired (any fork in the process).
static OS_FORK_PREPARE: AtomicU64 = AtomicU64::new(0);
static OS_FORK_PARENT: AtomicU64 = AtomicU64::new(0);
static OS_FORK_CHILD: AtomicU64 = AtomicU64::new(0);

/// Number of OS-level child forks observed while the shared runtime was NOT
/// parked by `before_fork`. A non-zero value means a fork bypassed ddtrace's
/// `os.register_at_fork` pre-fork hook and the child inherited a live runtime.
static BARE_FORK_LIVE_RUNTIME: AtomicU64 = AtomicU64::new(0);

/// True between `before_fork` (runtime torn down) and `after_fork_*` (runtime
/// rebuilt). Read by the child `pthread_atfork` handler to decide whether the
/// fork it is servicing went through the ddtrace pre-fork hook.
static RUNTIME_PARKED: AtomicBool = AtomicBool::new(false);

static REGISTER: Once = Once::new();

extern "C" {
    fn pthread_atfork(
        prepare: Option<unsafe extern "C" fn()>,
        parent: Option<unsafe extern "C" fn()>,
        child: Option<unsafe extern "C" fn()>,
    ) -> i32;
}

unsafe extern "C" fn atfork_prepare() {
    OS_FORK_PREPARE.fetch_add(1, Ordering::Relaxed);
}

unsafe extern "C" fn atfork_parent() {
    OS_FORK_PARENT.fetch_add(1, Ordering::Relaxed);
}

unsafe extern "C" fn atfork_child() {
    OS_FORK_CHILD.fetch_add(1, Ordering::Relaxed);
    if !RUNTIME_PARKED.load(Ordering::Relaxed) {
        BARE_FORK_LIVE_RUNTIME.fetch_add(1, Ordering::Relaxed);
    }
}

/// Register the `pthread_atfork` handlers exactly once for the process.
pub fn ensure_registered() {
    REGISTER.call_once(|| unsafe {
        pthread_atfork(
            Some(atfork_prepare),
            Some(atfork_parent),
            Some(atfork_child),
        );
    });
}

/// Record whether the runtime is currently parked (torn down for a fork).
pub fn set_runtime_parked(parked: bool) {
    RUNTIME_PARKED.store(parked, Ordering::Relaxed);
}

/// `(prepare, parent, child)` OS-level fork counts.
pub fn os_fork_counts() -> (u64, u64, u64) {
    (
        OS_FORK_PREPARE.load(Ordering::Relaxed),
        OS_FORK_PARENT.load(Ordering::Relaxed),
        OS_FORK_CHILD.load(Ordering::Relaxed),
    )
}

/// Child forks that inherited a live (non-parked) runtime, i.e. forks that
/// bypassed the ddtrace pre-fork hook.
pub fn bare_fork_live_runtime() -> u64 {
    BARE_FORK_LIVE_RUNTIME.load(Ordering::Relaxed)
}
