/// Object pool for libdatadog `Span<PyTraceData>` allocations.
///
/// # Memory reclamation
///
/// libdatadog spans contain several `Vec` and `HashMap` fields whose backing
/// buffers are allocated by glibc.  Under high span throughput glibc can
/// accumulate a large "high-water mark" because it rarely returns free pages to
/// the OS between short-lived allocations.  The pool addresses this in two ways:
///
/// 1. **Reuse**: instead of free+malloc on every span, we clear and recycle the
///    allocation, keeping the heap working set stable.
///
/// 2. **Batch trim**: when the pool grows past `max_size` we drop a batch of
///    `batch_trim_size` spans and call `malloc_trim(0)` (Linux/glibc only) to
///    return the now-free pages to the OS.
///
/// # Safety
///
/// Spans stored in the pool **must** have all Python references cleared before
/// they are inserted.  `SpanData::drop` guarantees this by resetting the span to
/// `Span::default()` (which sets all `PyBackedString` fields to the static-empty
/// variant whose `storage` is `None`) before calling `release`.  The pool's
/// `Vec` can therefore be dropped at process exit without holding the GIL.
use std::sync::{Mutex, OnceLock};

use libdd_trace_utils::span::v04::Span;

use crate::py_string::PyTraceData;

// DEV: These defaults can be overridden at runtime via env vars; see `get_pool`.
const DEFAULT_MAX_POOL_SIZE: usize = 1024;
const DEFAULT_BATCH_TRIM_SIZE: usize = 128;

/// Generic object pool backed by a `Mutex<Vec<T>>`.
///
/// When the pool exceeds `max_size`, the overflow item is dropped together with
/// a batch of `batch_trim_size` existing pool entries, then `malloc_trim(0)` is
/// called to return freed pages to the OS on Linux/glibc.
pub(super) struct ObjectPool<T> {
    inner: Mutex<Vec<T>>,
    max_size: usize,
    batch_trim_size: usize,
}

impl<T: Default> ObjectPool<T> {
    pub(super) fn new(max_size: usize, batch_trim_size: usize) -> Self {
        ObjectPool {
            // Pre-allocate to max_size so the Vec never reallocates under load.
            inner: Mutex::new(Vec::with_capacity(max_size)),
            max_size,
            batch_trim_size,
        }
    }

    /// Pop an item from the pool, allocating a fresh default when it is empty.
    pub(super) fn acquire(&self) -> T {
        let mut pool = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        pool.pop().unwrap_or_default()
    }

    /// Return an item to the pool.
    ///
    /// When the pool is at capacity the item is dropped alongside a batch of
    /// existing entries, and `malloc_trim(0)` is called afterward (Linux/glibc
    /// only) to reclaim freed pages.  Dropped items are drained outside the
    /// lock so peers are not blocked during their deallocation.
    pub(super) fn release(&self, item: T) {
        // `evicted` holds items that need to be dropped outside the lock.
        let mut evicted: Vec<T> = Vec::new();
        let should_trim = {
            let mut pool = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            if pool.len() < self.max_size {
                pool.push(item);
                false
            } else {
                // Pool at capacity: collect the overflow item plus a batch of
                // existing entries to be dropped after the lock is released.
                evicted.push(item);
                let trim_count = self.batch_trim_size.min(pool.len());
                let new_len = pool.len() - trim_count;
                evicted.extend(pool.drain(new_len..));
                true
            }
        }; // lock released here; evicted items are dropped below

        drop(evicted); // dealloc outside the lock
        if should_trim {
            trim_heap();
        }
    }

    /// Current number of items held in the pool (test-only).
    #[cfg(test)]
    pub(super) fn len(&self) -> usize {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).len()
    }
}

/// Tell glibc to return free pages to the OS.  No-op on non-glibc targets.
///
/// Guard is `target_env = "gnu"` (not just `target_os = "linux"`) because
/// `malloc_trim` is a glibc extension absent from musl and other libc
/// implementations.  Using `target_os = "linux"` would cause a link-time
/// symbol-not-found error on musllinux wheels.
#[inline]
fn trim_heap() {
    #[cfg(all(target_os = "linux", target_env = "gnu"))]
    {
        extern "C" {
            fn malloc_trim(pad: usize) -> i32;
        }
        // SAFETY: malloc_trim is a standard glibc function with no preconditions
        // beyond a valid pad argument (0 = trim as aggressively as possible).
        unsafe {
            malloc_trim(0);
        }
    }
}

/// Process-wide pool for libdatadog `Span<PyTraceData>` allocations.
pub(super) type SpanPool = ObjectPool<Box<Span<PyTraceData>>>;

static POOL: OnceLock<SpanPool> = OnceLock::new();

/// Return the process-wide span pool, initialising it on first call.
///
/// Pool parameters are read from environment variables on first use:
///
/// * `DD_SPAN_POOL_MAX_SIZE` – maximum number of spans to keep idle
///   (default: 1024).
/// * `DD_SPAN_POOL_BATCH_TRIM_SIZE` – number of spans to evict when the pool
///   exceeds `max_size` before calling `malloc_trim` (default: 128).
pub(super) fn get_pool() -> &'static SpanPool {
    POOL.get_or_init(|| {
        let max_size = read_env_usize("DD_SPAN_POOL_MAX_SIZE", DEFAULT_MAX_POOL_SIZE);
        let batch_trim_size =
            read_env_usize("DD_SPAN_POOL_BATCH_TRIM_SIZE", DEFAULT_BATCH_TRIM_SIZE);
        SpanPool::new(max_size, batch_trim_size)
    })
}

fn read_env_usize(key: &str, default: usize) -> usize {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Tests use Box<u32> to avoid pulling in pyo3 symbols (the test binary is not
    // linked against Python, so using Span<PyTraceData> would fail to link).
    type Item = Box<u32>;
    type Pool = ObjectPool<Item>;

    fn make_pool() -> Pool {
        Pool::new(4, 2)
    }

    #[test]
    fn acquire_from_empty_pool_allocates() {
        let pool = make_pool();
        let item = pool.acquire();
        assert_eq!(pool.len(), 0);
        drop(item);
    }

    #[test]
    fn release_then_acquire_reuses_allocation() {
        let pool = make_pool();
        let item = pool.acquire();
        let ptr = &*item as *const u32 as usize;
        pool.release(item);
        assert_eq!(pool.len(), 1);
        let item2 = pool.acquire();
        assert_eq!(
            &*item2 as *const u32 as usize, ptr,
            "must reuse the same allocation"
        );
        assert_eq!(pool.len(), 0);
        drop(item2);
    }

    #[test]
    fn pool_at_capacity_drops_overflow_and_trims_batch() {
        let pool = make_pool(); // max_size=4, batch_trim_size=2
        for _ in 0..4 {
            pool.release(Box::new(0u32));
        }
        assert_eq!(pool.len(), 4);

        // Releasing one more should drop the item + evict a batch of 2.
        pool.release(Box::new(99u32));
        assert_eq!(
            pool.len(),
            4 - 2,
            "batch of batch_trim_size=2 should have been evicted"
        );
    }

    #[test]
    fn acquire_from_non_empty_pool_decrements_len() {
        let pool = make_pool();
        pool.release(Box::new(1u32));
        pool.release(Box::new(2u32));
        assert_eq!(pool.len(), 2);
        let _ = pool.acquire();
        assert_eq!(pool.len(), 1);
        let _ = pool.acquire();
        assert_eq!(pool.len(), 0);
    }
}
