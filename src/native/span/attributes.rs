use crate::py_string::PyBackedString;
use pyo3::{Bound, IntoPyObject as _, PyAny, Python};
use rustc_hash::FxHashMap;
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

    /// Cheap refcount bump — the only GIL-bound step needed to snapshot a key for
    /// later GIL-free processing (see `SpanData::snapshot`).
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

/// Typed storage for a single span attribute value.
///
/// Each variant is one machine word (8 bytes); the enum is 16 bytes total
/// (8-byte payload + discriminant + alignment). Keeping all variants at one
/// word avoids the slot-size bloat that a `PyBackedString` value would impose
/// on numeric-only attributes.
///
/// Bool is intentionally absent — `extract::<i64>()` succeeds for Python bool
/// (True → 1, False → 0), so bool collapses into `Int` at write time.
pub(crate) enum AttributeValue {
    /// A Python str, stored GIL-free-readable (see [`PyBackedString`]).
    Str(PyBackedString),
    Int(i64),
    Float(f64),
}

impl AttributeValue {
    pub(crate) fn traverse(&self, visit: &pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let AttributeValue::Str(s) = self {
            s.traverse(visit)?;
        }
        Ok(())
    }

    /// Return the natural Python object for this value (no v0.4 projection).
    /// Str → str, Int → int, Float → float.
    pub(crate) fn as_py<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        match self {
            AttributeValue::Str(s) => s.as_py(py),
            AttributeValue::Int(i) => i.into_pyobject(py).expect("i64 into_pyobject").into_any(),
            AttributeValue::Float(f) => f.into_pyobject(py).expect("f64 into_pyobject").into_any(),
        }
    }

    /// Cheap snapshot for later GIL-free processing: `Str` bumps the backing
    /// refcount, `Int`/`Float` are plain `Copy`.
    pub(crate) fn clone_ref(&self, py: Python<'_>) -> AttributeValue {
        match self {
            AttributeValue::Str(s) => AttributeValue::Str(s.clone_ref(py)),
            AttributeValue::Int(i) => AttributeValue::Int(*i),
            AttributeValue::Float(f) => AttributeValue::Float(*f),
        }
    }
}

/// Span attribute map.
///
/// `FxHashMap` over `std::collections::HashMap` — keys are short, trusted tag
/// names from instrumented Python code, so SipHash DoS resistance is unnecessary.
/// FxHash is faster on short keys and `FxBuildHasher` is a ZST (saves the
/// 16-byte `RandomState` per map instance).
pub(crate) type AttributeMap = FxHashMap<AttrKey, AttributeValue>;
