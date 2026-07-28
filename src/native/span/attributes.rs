use crate::py_string::PyBackedString;
use libdd_trace_utils::span::v04::VecMap;
use pyo3::types::PyString;
use pyo3::{Bound, IntoPyObject as _, Py, PyAny, Python};
use std::cell::RefCell;
use std::ops::{Deref, DerefMut};
use std::thread::LocalKey;

/// Typed storage for a single numeric span attribute (a `metrics` entry).
///
/// Bool is intentionally absent — `extract::<i64>()` succeeds for Python bool
/// (True → 1, False → 0), so bool collapses into `Int` at write time.
#[derive(Clone, Copy)]
pub(crate) enum MetricValue {
    Int(i64),
    Float(f64),
}

impl MetricValue {
    /// Int → Python int, Float → Python float.
    pub(crate) fn as_py<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        match self {
            MetricValue::Int(i) => i.into_pyobject(py).expect("i64 into_pyobject").into_any(),
            MetricValue::Float(f) => f.into_pyobject(py).expect("f64 into_pyobject").into_any(),
        }
    }

    /// Value projected into the v0.4 wire `metrics` type (always `f64`).
    #[allow(dead_code)]
    pub(crate) fn as_f64(&self) -> f64 {
        match self {
            MetricValue::Int(i) => *i as f64,
            MetricValue::Float(f) => *f,
        }
    }
}

/// Span `meta` map: string-valued tags.
pub(crate) type MetaMap = Pooled<MetaPool>;

/// Span `metrics` map: numeric-valued tags. Mutually exclusive with [`MetaMap`] — a key lives in
/// exactly one of the two; `SpanData`'s attribute methods enforce this.
pub(crate) type MetricsMap = Pooled<MetricsPool>;

// --- Thread-local backing-buffer recycle pool ----------------------------------------------------
// Allocating a `meta`/`metrics` backing `Vec` per span is millions of short-lived allocations under
// load (the top per-span native allocator under memray). Instead, a dropped map's buffer is cleared
// and returned to a per-thread pool, and the next span's first insert pops it instead of
// allocating. Acquire and recycle run on the same thread, so no locking is needed; pooled buffers
// are always empty, so they hold no Python references and are safe across requests and forks.
//
// The caps bound retained memory (~0.5 MB per thread, both pools) and only bind under bursty or
// outlier load: ATTR_POOL_MAX caps buffers per map per thread — a trace frees its spans in a burst
// at finish, so 128 covers several concurrent traces — and ATTR_POOL_BUF_CAP keeps an outlier span
// with hundreds of tags from parking an oversized Vec for the thread's life.
const ATTR_POOL_MAX: usize = 128;
const ATTR_POOL_BUF_CAP: usize = 64;

/// One recycled backing buffer: a `VecMap`'s row `Vec`, kept empty (capacity only) for reuse.
type PoolBuf<K, V> = Vec<(K, V)>;
/// A map's thread-local stack of recycled buffers.
type PoolCell<K, V> = RefCell<Vec<PoolBuf<K, V>>>;
type PoolRef<K, V> = &'static LocalKey<PoolCell<K, V>>;

thread_local! {
    static META_POOL: PoolCell<PyBackedString, Py<PyString>> = const { RefCell::new(Vec::new()) };
    static METRICS_POOL: PoolCell<PyBackedString, MetricValue> = const { RefCell::new(Vec::new()) };
}

/// Take a recycled backing buffer from `pool`, or allocate one presized to `floor` on a miss.
fn pool_acquire<K, V>(pool: PoolRef<K, V>, floor: usize) -> VecMap<K, V> {
    match pool.with(|p| p.borrow_mut().pop()) {
        Some(backing) => VecMap::from(backing),
        None => VecMap::with_capacity(floor),
    }
}

/// Clear `map`'s backing buffer and return it to `pool` for reuse. Drops it instead if the map
/// never allocated or the pool is already at `ATTR_POOL_MAX`.
fn pool_recycle<K, V>(pool: PoolRef<K, V>, map: VecMap<K, V>) {
    let mut backing: Vec<(K, V)> = map.into();
    // Nothing to reclaim from a map that never allocated (the common no-metrics span); let an
    // outlier oversized buffer free rather than hoard it.
    let cap = backing.capacity();
    if cap == 0 || cap > ATTR_POOL_BUF_CAP {
        return;
    }
    backing.clear();
    pool.with(|p| {
        let mut p = p.borrow_mut();
        if p.len() < ATTR_POOL_MAX {
            p.push(backing);
        }
    });
}

/// Associates a map's value type with its thread-local pool and presize floor, so [`Pooled`]
/// routes acquire/recycle generically (one wrapper, not per-type).
pub(crate) trait Pool {
    /// `'static` because the pool is a `thread_local!` (`&'static LocalKey`).
    type V: 'static;
    /// Capacity a fresh backing buffer is presized to on a pool miss.
    const PRESIZE: usize;
    fn local() -> PoolRef<PyBackedString, Self::V>;
}

/// `meta` pool marker — meta carries most of a span's tags.
pub(crate) struct MetaPool;
impl Pool for MetaPool {
    type V = Py<PyString>;
    const PRESIZE: usize = 8;
    fn local() -> PoolRef<PyBackedString, Self::V> {
        &META_POOL
    }
}

/// `metrics` pool marker — metrics is usually a handful of numeric tags.
pub(crate) struct MetricsPool;
impl Pool for MetricsPool {
    type V = MetricValue;
    const PRESIZE: usize = 4;
    fn local() -> PoolRef<PyBackedString, Self::V> {
        &METRICS_POOL
    }
}

/// A libdatadog [`VecMap`] whose backing buffer is drawn from and returned to a thread-local
/// recycle pool. Reads reach the inner `VecMap` through `Deref`/`DerefMut`; note its duplicate
/// semantics — `insert` is a plain append, `get` scans from the back so the last write wins, and
/// `remove_slow` clears every occurrence of a key.
pub(crate) struct Pooled<P: Pool> {
    map: VecMap<PyBackedString, P::V>,
    /// Set on the first pool acquisition; distinguishes "never touched" (skip recycling on drop)
    /// from "emptied via removes" (still holds a backing buffer worth recycling).
    acquired: bool,
}

impl<P: Pool> Default for Pooled<P> {
    fn default() -> Self {
        Self {
            map: VecMap::default(),
            acquired: false,
        }
    }
}

impl<P: Pool> Deref for Pooled<P> {
    type Target = VecMap<PyBackedString, P::V>;
    fn deref(&self) -> &Self::Target {
        &self.map
    }
}

impl<P: Pool> DerefMut for Pooled<P> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.map
    }
}

impl<P: Pool> Drop for Pooled<P> {
    /// Return the backing buffer to the pool. Runs during `SpanData` dealloc / `__clear__` reset
    /// (GIL held), so clearing it -- which drops the `Py` keys/values -- is safe.
    fn drop(&mut self) {
        if self.acquired {
            pool_recycle(P::local(), std::mem::take(&mut self.map));
        }
    }
}

impl<P: Pool> Pooled<P> {
    /// Acquire a buffer from the pool on the first insert, so a map never inserted into never
    /// allocates. Shadows the inner `VecMap::insert` (reachable only via `Deref`), so a plain
    /// `.insert(..)` on a span's `meta`/`metrics` always goes through the pool.
    pub(crate) fn insert(&mut self, key: PyBackedString, value: P::V) {
        if !self.acquired {
            self.map = pool_acquire(P::local(), P::PRESIZE);
            self.acquired = true;
        }
        self.map.insert(key, value);
    }
}

#[cfg(test)]
mod tests {
    use libdd_trace_utils::span::v04::VecMap;

    thread_local! {
        static TEST_POOL: std::cell::RefCell<Vec<Vec<(String, i32)>>> =
            const { std::cell::RefCell::new(Vec::new()) };
    }

    #[test]
    fn pool_acquire_miss_presizes_recycle_then_hit_reuses() {
        TEST_POOL.with(|p| p.borrow_mut().clear());
        // miss -> allocate at the floor
        let mut m = super::pool_acquire(&TEST_POOL, 8);
        m.insert("a".to_string(), 1);
        m.insert("b".to_string(), 2);
        let backing: Vec<(String, i32)> = m.into();
        let cap = backing.capacity();
        assert!(cap >= 8);
        let m: VecMap<String, i32> = backing.into();
        // recycle -> buffer returns to the pool
        super::pool_recycle(&TEST_POOL, m);
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), 1);
        // hit -> reuse the same cleared buffer, no realloc
        let m2 = super::pool_acquire(&TEST_POOL, 8);
        let backing2: Vec<(String, i32)> = m2.into();
        assert_eq!(backing2.capacity(), cap);
        assert!(backing2.is_empty());
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), 0);
    }

    #[test]
    fn pool_recycle_respects_max_and_skips_unallocated() {
        TEST_POOL.with(|p| p.borrow_mut().clear());
        // a never-allocated map is not pooled
        let empty: VecMap<String, i32> = VecMap::default();
        super::pool_recycle(&TEST_POOL, empty);
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), 0);
        // recycling past the cap leaves the pool at exactly ATTR_POOL_MAX
        for _ in 0..(super::ATTR_POOL_MAX + 10) {
            let mut backing: Vec<(String, i32)> = Vec::with_capacity(4);
            backing.push(("x".to_string(), 1));
            let m: VecMap<String, i32> = backing.into();
            super::pool_recycle(&TEST_POOL, m);
        }
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), super::ATTR_POOL_MAX);
    }

    #[test]
    fn pool_recycle_drops_oversized_buffer() {
        TEST_POOL.with(|p| p.borrow_mut().clear());
        let backing: Vec<(String, i32)> = Vec::with_capacity(super::ATTR_POOL_BUF_CAP + 1);
        let m: VecMap<String, i32> = backing.into();
        super::pool_recycle(&TEST_POOL, m);
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), 0);
    }
}
