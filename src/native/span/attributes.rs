use crate::py_string::PyBackedString;
use pyo3::types::PyString;
use pyo3::{Bound, IntoPyObject as _, Py, PyAny, Python};
use std::borrow::Borrow;
use std::hash::{Hash, Hasher};

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
/// Chosen over `FxHashMap` (a per-span hash map that grows/rehashes on the hot path) and over
/// libdatadog's `VecMap` (whose `insert` blindly *appends* — duplicate-tolerant, last-read-wins —
/// and whose `get` returns the *first* match, breaking last-wins `set_tag`; it also lacks a
/// value-returning `remove` and a `keys` iterator). Spans hold ~20 tags or fewer, so a linear
/// scan is competitive with hashing while avoiding all hashing/rehashing allocation.
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

/// Span `meta` map: string-valued tags, keyed by attribute name.
/// Values keep the original `Py<PyString>` so post-finish reads are zero-copy; the wire span's
/// `PyBackedString` view is materialized only at encode time (`SpanData::build_v04_span`).
/// Mutually exclusive with [`MetricsMap`].
pub(crate) type MetaMap = VecStore<AttrKey, Py<PyString>>;

/// Span `metrics` map: numeric-valued tags, keyed by attribute name.
/// Mutually exclusive with [`MetaMap`] — a given key lives in exactly one of the two maps at a
/// time; `SpanData`'s attribute methods enforce this.
pub(crate) type MetricsMap = VecStore<AttrKey, MetricValue>;

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
