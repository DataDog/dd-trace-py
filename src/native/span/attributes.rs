use crate::py_string::PyBackedString;
use pyo3::types::PyString;
use pyo3::{Bound, IntoPyObject as _, Py, PyAny, Python};
use std::borrow::Borrow;
use std::cell::RefCell;
use std::hash::{Hash, Hasher};
use std::ops::{Deref, DerefMut};
use std::thread::LocalKey;

/// Map key for span attributes — a GIL-free-readable Python str (see
/// [`PyBackedString`]). `Hash`, `Eq`, and `Borrow<str>` dispatch straight to the
/// backing `&str` via `Deref`, no GIL required and no raw FFI calls.
pub(crate) struct AttrKey(PyBackedString);

impl AttrKey {
    pub(crate) fn new(s: PyBackedString) -> Self {
        Self(s)
    }

    pub(crate) fn as_bound<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        self.0.as_py(py)
    }

    /// Borrow the key's UTF-8 content (GIL-free — reads through `PyBackedString`'s raw pointer).
    /// Used to evict the key from the sibling map to preserve meta/metrics mutual exclusion.
    pub(crate) fn as_str(&self) -> &str {
        &self.0
    }

    /// Cheap refcount bump producing a GIL-free-readable key for the wire span
    /// (see `SpanData::build_v04_span`).
    pub(crate) fn clone_ref(&self, py: Python<'_>) -> PyBackedString {
        self.0.clone_ref(py)
    }

    pub(crate) fn traverse(&self, visit: &pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        self.0.traverse(visit)
    }
}

impl Hash for AttrKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.hash(state);
    }
}

impl PartialEq for AttrKey {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0
    }
}

impl Eq for AttrKey {}

impl Borrow<str> for AttrKey {
    fn borrow(&self) -> &str {
        self.0.borrow()
    }
}

/// Typed storage for a single numeric span attribute (a `metrics` entry).
///
/// Both variants are one machine word; the enum is 16 bytes total (8-byte
/// payload + discriminant + alignment).
///
/// Bool is intentionally absent — `extract::<i64>()` succeeds for Python bool
/// (True → 1, False → 0), so bool collapses into `Int` at write time.
#[derive(Clone, Copy)]
pub(crate) enum MetricValue {
    Int(i64),
    Float(f64),
}

impl MetricValue {
    /// Return the natural Python object for this value (no v0.4 projection).
    /// Int → int, Float → float.
    pub(crate) fn as_py<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        match self {
            MetricValue::Int(i) => i.into_pyobject(py).expect("i64 into_pyobject").into_any(),
            MetricValue::Float(f) => f.into_pyobject(py).expect("f64 into_pyobject").into_any(),
        }
    }

    /// Value projected into the v0.4 wire `metrics` type (always `f64`).
    pub(crate) fn as_f64(&self) -> f64 {
        match self {
            MetricValue::Int(i) => *i as f64,
            MetricValue::Float(f) => *f,
        }
    }
}

/// A tiny Vec-backed associative map with **last-wins** insert semantics, used for a span's
/// `meta`/`metrics` stores.
///
/// A span holds ~20 tags or fewer, so a linear scan beats a hash map and avoids per-span
/// hashing/rehashing allocation. Last-wins is why libdatadog's `VecMap` won't do here: its
/// `insert` appends duplicates and its `get` returns the first match, which breaks `set_tag`.
///
/// # Invariant
///
/// `insert` overwrites the existing row for a key in place (never appends a duplicate), so every
/// key appears at most once. Combined with `SpanData`'s mutual-exclusion (`insert_meta`/
/// `insert_metric` evict the key from the other map), a given key lives in exactly one row of
/// exactly one map. Callers rely on this to skip any downstream dedup pass.
pub(crate) struct VecStore<K, V> {
    data: Vec<(K, V)>,
}

impl<K, V> Default for VecStore<K, V> {
    fn default() -> Self {
        Self { data: Vec::new() }
    }
}

impl<K, V> VecStore<K, V> {
    #[allow(dead_code)]
    pub(crate) fn new() -> Self {
        Self { data: Vec::new() }
    }

    #[allow(dead_code)]
    pub(crate) fn with_capacity(n: usize) -> Self {
        Self {
            data: Vec::with_capacity(n),
        }
    }

    /// Backing capacity (0 = nothing allocated yet). Used to detect a store's first insert.
    pub(crate) fn capacity(&self) -> usize {
        self.data.capacity()
    }

    /// Wrap an (empty) backing buffer handed back by the recycle pool; its capacity is the store's
    /// head start.
    fn from_backing(data: Vec<(K, V)>) -> Self {
        debug_assert!(data.is_empty());
        Self { data }
    }

    /// Take the backing buffer out, leaving the store empty (capacity 0), to hand the allocation to
    /// the recycle pool.
    fn take_backing(&mut self) -> Vec<(K, V)> {
        std::mem::take(&mut self.data)
    }

    pub(crate) fn len(&self) -> usize {
        self.data.len()
    }

    #[allow(dead_code)]
    pub(crate) fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    #[allow(dead_code)]
    pub(crate) fn clear(&mut self) {
        self.data.clear()
    }

    pub(crate) fn iter(&self) -> core::slice::Iter<'_, (K, V)> {
        self.data.iter()
    }

    pub(crate) fn keys(&self) -> impl Iterator<Item = &K> {
        self.data.iter().map(|(k, _)| k)
    }

    /// Consume the map into its backing `Vec` (used when moving entries into a wire span).
    #[allow(dead_code)]
    pub(crate) fn into_vec(self) -> Vec<(K, V)> {
        self.data
    }
}

impl<K: PartialEq + Borrow<str>, V> VecStore<K, V> {
    /// Last-wins insert: overwrite the value if the key is already present, otherwise push a new
    /// row. Never creates a duplicate key.
    pub(crate) fn insert(&mut self, k: K, v: V) {
        match self.data.iter_mut().find(|(ek, _)| *ek == k) {
            Some(slot) => slot.1 = v,
            None => self.data.push((k, v)),
        }
    }

    /// Remove the entry for `k`, returning its value. `swap_remove` (O(1)) is safe because tag
    /// order is irrelevant to the wire format.
    pub(crate) fn remove(&mut self, k: &str) -> Option<V> {
        let i = self.data.iter().position(|(ek, _)| ek.borrow() == k)?;
        Some(self.data.swap_remove(i).1)
    }

    pub(crate) fn get(&self, k: &str) -> Option<&V> {
        self.data
            .iter()
            .find(|(ek, _)| ek.borrow() == k)
            .map(|(_, v)| v)
    }

    pub(crate) fn contains_key(&self, k: &str) -> bool {
        self.data.iter().any(|(ek, _)| ek.borrow() == k)
    }
}

/// Span `meta` map: string-valued tags, keyed by attribute name. A [`Pooled`] `VecStore` (backing
/// buffer recycled through the thread-local pool). Values keep the original `Py<PyString>` so
/// post-finish reads are zero-copy; the wire span's `PyBackedString` view is materialized only at
/// encode time (`SpanData::build_v04_span`). Mutually exclusive with [`MetricsMap`].
pub(crate) type MetaMap = Pooled<MetaPool>;

/// Span `metrics` map: numeric-valued tags, keyed by attribute name. A [`Pooled`] `VecStore`.
/// Mutually exclusive with [`MetaMap`] — a given key lives in exactly one of the two maps at a
/// time; `SpanData`'s attribute methods enforce this.
pub(crate) type MetricsMap = Pooled<MetricsPool>;

// --- Thread-local backing-buffer recycle pool ----------------------------------------------------
// A span allocates a `meta`/`metrics` backing `Vec` on its first insert and frees it on drop; under
// load that is millions of short-lived allocations (the top per-span native allocator under
// memray). Instead, recycle the buffers: on drop, clear a store's buffer (dropping its entries) and
// return the allocation to a per-thread pool; the next span's first insert pops a buffer from the
// pool instead of allocating. Acquire and recycle run on the same (request) thread, so a plain
// thread-local needs no locking; pooled buffers are always empty, so they hold no Python references
// and are safe to retain across requests and across a fork.
//
// ATTR_POOL_MAX bounds retained buffers per map per thread; ATTR_POOL_BUF_CAP bounds each buffer's
// capacity so a rare outlier span with hundreds of tags can't park an oversized Vec in the pool for
// the thread's life. A trace frees its spans in a burst at finish (tens of buffers); 128 covers
// several concurrent traces' worth with headroom, and with both caps the retained memory is bounded
// at ~128 * 64 * entry-size (tens of KB per thread). The caps only bind under bursty/outlier load;
// in steady state the pool hovers around the working set.
const ATTR_POOL_MAX: usize = 128;
const ATTR_POOL_BUF_CAP: usize = 64;

/// One recycled backing buffer: a `VecStore`'s row `Vec`, kept empty (capacity only) for reuse.
type PoolBuf<K, V> = Vec<(K, V)>;
/// A map's thread-local stack of recycled buffers.
type PoolCell<K, V> = RefCell<Vec<PoolBuf<K, V>>>;
/// `'static` handle to a thread-local recycle pool (what `Pool::local` returns, and the pool fns take).
type PoolRef<K, V> = &'static LocalKey<PoolCell<K, V>>;

thread_local! {
    static META_POOL: PoolCell<AttrKey, Py<PyString>> = const { RefCell::new(Vec::new()) };
    static METRICS_POOL: PoolCell<AttrKey, MetricValue> = const { RefCell::new(Vec::new()) };
}

/// Take a recycled backing buffer from `pool`, or allocate one presized to `floor` on a miss.
fn pool_acquire<K, V>(pool: PoolRef<K, V>, floor: usize) -> VecStore<K, V> {
    let backing = pool
        .with(|p| p.borrow_mut().pop())
        .unwrap_or_else(|| Vec::with_capacity(floor));
    VecStore::from_backing(backing)
}

/// Clear `store`'s backing buffer and return it to `pool` for reuse. Drops it instead if the store
/// never allocated or the pool is already at `ATTR_POOL_MAX`.
fn pool_recycle<K, V>(pool: PoolRef<K, V>, store: &mut VecStore<K, V>) {
    // Don't reclaim a store that never allocated (the common no-metrics span -- skip the `mem::take`
    // entirely) or an outlier oversized buffer (let it free rather than hoard it). Both just let the
    // store's Vec drop normally. This runs on every span drop.
    let cap = store.capacity();
    if cap == 0 || cap > ATTR_POOL_BUF_CAP {
        return;
    }
    let mut backing = store.take_backing();
    backing.clear();
    pool.with(|p| {
        let mut p = p.borrow_mut();
        if p.len() < ATTR_POOL_MAX {
            p.push(backing);
        }
    });
}

/// Associates a span-attribute value type with its thread-local recycle pool and first-insert
/// presize floor, so [`Pooled`] routes acquire/recycle generically (one wrapper, not per-type).
pub(crate) trait Pool {
    /// The map's value type (`meta` → `Py<PyString>`, `metrics` → `MetricValue`). `'static` because
    /// the pool is a `thread_local!` (`&'static LocalKey`).
    type V: 'static;
    /// Capacity a fresh backing buffer is presized to on a pool miss.
    const PRESIZE: usize;
    fn local() -> PoolRef<AttrKey, Self::V>;
}

/// `meta` pool marker — meta carries most of a span's tags.
pub(crate) struct MetaPool;
impl Pool for MetaPool {
    type V = Py<PyString>;
    const PRESIZE: usize = 8;
    fn local() -> PoolRef<AttrKey, Self::V> {
        &META_POOL
    }
}

/// `metrics` pool marker — metrics is usually a handful of numeric tags.
pub(crate) struct MetricsPool;
impl Pool for MetricsPool {
    type V = MetricValue;
    const PRESIZE: usize = 4;
    fn local() -> PoolRef<AttrKey, Self::V> {
        &METRICS_POOL
    }
}

/// A [`VecStore`] whose backing buffer is drawn from and returned to a thread-local recycle pool.
/// Starts empty (no allocation until the first insert); its `Drop` returns the buffer to the pool.
/// Read/scan methods reach the inner `VecStore` through `Deref`/`DerefMut`.
pub(crate) struct Pooled<P: Pool>(VecStore<AttrKey, P::V>);

impl<P: Pool> Default for Pooled<P> {
    fn default() -> Self {
        Self(VecStore::default())
    }
}

impl<P: Pool> Deref for Pooled<P> {
    type Target = VecStore<AttrKey, P::V>;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl<P: Pool> DerefMut for Pooled<P> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}

impl<P: Pool> Drop for Pooled<P> {
    /// Return the backing buffer to the pool. Runs during `SpanData` dealloc / `__clear__` reset
    /// (GIL held), so clearing it -- which drops the `Py` keys/values -- is safe. A no-op on a
    /// store that never allocated.
    fn drop(&mut self) {
        pool_recycle(P::local(), &mut self.0);
    }
}

impl<P: Pool> Pooled<P> {
    /// Last-wins insert. On the store's first insert (still empty) acquire a recycled or freshly
    /// presized buffer from the pool; a store never inserted into never allocates. Shadows the
    /// inner `VecStore::insert` (reachable only via `Deref`), so a plain `.insert(..)` on a span's
    /// `meta`/`metrics` always goes through the pool.
    pub(crate) fn insert(&mut self, key: AttrKey, value: P::V) {
        if self.0.capacity() == 0 {
            self.0 = pool_acquire(P::local(), P::PRESIZE);
        }
        self.0.insert(key, value);
    }
}

#[cfg(test)]
mod tests {
    use super::VecStore;

    #[test]
    fn insert_appends_distinct_keys() {
        let mut m: VecStore<String, i32> = VecStore::new();
        m.insert("a".to_string(), 1);
        m.insert("b".to_string(), 2);
        assert_eq!(m.len(), 2);
        assert_eq!(m.get("a"), Some(&1));
        assert_eq!(m.get("b"), Some(&2));
    }

    #[test]
    fn insert_overwrites_last_wins() {
        let mut m: VecStore<String, i32> = VecStore::new();
        m.insert("a".to_string(), 1);
        m.insert("a".to_string(), 2);
        m.insert("a".to_string(), 3);
        // Overwrite in place — no duplicate row — and the last write wins.
        assert_eq!(m.len(), 1);
        assert_eq!(m.get("a"), Some(&3));
    }

    #[test]
    fn remove_returns_value_and_deletes() {
        let mut m: VecStore<String, i32> = VecStore::new();
        m.insert("a".to_string(), 1);
        m.insert("b".to_string(), 2);
        assert_eq!(m.remove("a"), Some(1));
        // Second remove of the same key finds nothing.
        assert_eq!(m.remove("a"), None);
        assert!(!m.contains_key("a"));
        assert!(m.contains_key("b"));
        assert_eq!(m.len(), 1);
    }

    #[test]
    fn get_and_contains_absent_key() {
        let m: VecStore<String, i32> = VecStore::new();
        assert_eq!(m.get("x"), None);
        assert!(!m.contains_key("x"));
    }

    #[test]
    fn iter_and_keys_visit_every_entry() {
        let mut m: VecStore<String, i32> = VecStore::with_capacity(2);
        m.insert("a".to_string(), 1);
        m.insert("b".to_string(), 2);
        let mut pairs: Vec<(&str, i32)> = m.iter().map(|(k, v)| (k.as_str(), *v)).collect();
        pairs.sort();
        assert_eq!(pairs, vec![("a", 1), ("b", 2)]);
        let mut keys: Vec<&str> = m.keys().map(|k| k.as_str()).collect();
        keys.sort();
        assert_eq!(keys, vec!["a", "b"]);
    }

    #[test]
    fn into_vec_moves_all_data() {
        let mut m: VecStore<String, i32> = VecStore::new();
        m.insert("a".to_string(), 1);
        m.insert("a".to_string(), 9); // overwrite still yields a single row
        assert_eq!(m.into_vec(), vec![("a".to_string(), 9)]);
    }

    thread_local! {
        static TEST_POOL: std::cell::RefCell<Vec<Vec<(String, i32)>>> =
            const { std::cell::RefCell::new(Vec::new()) };
    }

    #[test]
    fn pool_acquire_miss_presizes_recycle_then_hit_reuses() {
        TEST_POOL.with(|p| p.borrow_mut().clear());
        // miss -> allocate at the floor
        let mut m = super::pool_acquire(&TEST_POOL, 8);
        assert!(m.capacity() >= 8);
        m.insert("a".to_string(), 1);
        m.insert("b".to_string(), 2);
        let cap = m.capacity();
        // recycle -> buffer returns to the pool, store left empty
        super::pool_recycle(&TEST_POOL, &mut m);
        assert_eq!(m.capacity(), 0);
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), 1);
        // hit -> reuse the same cleared buffer, no realloc
        let m2: VecStore<String, i32> = super::pool_acquire(&TEST_POOL, 8);
        assert_eq!(m2.capacity(), cap);
        assert!(m2.is_empty());
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), 0);
    }

    #[test]
    fn pool_recycle_respects_max_and_skips_unallocated() {
        TEST_POOL.with(|p| p.borrow_mut().clear());
        // a never-allocated store is not pooled
        let mut empty: VecStore<String, i32> = VecStore::new();
        super::pool_recycle(&TEST_POOL, &mut empty);
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), 0);
        // recycling past the cap leaves the pool at exactly ATTR_POOL_MAX
        for _ in 0..(super::ATTR_POOL_MAX + 10) {
            let mut m: VecStore<String, i32> = VecStore::with_capacity(4);
            m.insert("x".to_string(), 1);
            super::pool_recycle(&TEST_POOL, &mut m);
        }
        assert_eq!(TEST_POOL.with(|p| p.borrow().len()), super::ATTR_POOL_MAX);
    }

    #[test]
    fn is_empty_and_clear() {
        let mut m: VecStore<String, i32> = VecStore::new();
        assert!(m.is_empty());
        m.insert("a".to_string(), 1);
        assert!(!m.is_empty());
        m.clear();
        assert!(m.is_empty());
        assert_eq!(m.get("a"), None);
    }
}
