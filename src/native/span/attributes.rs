use pyo3::{ffi, types::PyString, Bound, IntoPyObject as _, Py, PyAny, Python};
use rustc_hash::FxHashMap;
use std::borrow::Borrow;
use std::hash::{Hash, Hasher};

/// 8-byte map key for span attributes — owns a reference to a Python str object.
/// `Hash`, `Eq`, and `Borrow<str>` dispatch to the string's UTF-8 content via
/// `PyUnicode_AsUTF8AndSize`, which CPython caches on the string object after
/// the first call; per-op cost is O(1).
///
/// # Safety contract
///
/// `Hash`, `Eq`, and `Borrow<str>` call `PyUnicode_AsUTF8AndSize` using the raw
/// `PyObject` pointer. This is safe because the GIL is held by every caller — all
/// `AttributeMap` operations occur inside `#[pymethods]` entry points.
/// `pub(crate)` visibility enforces this: callers outside the crate cannot
/// construct an `AttrKey` and trigger `Hash`/`Eq` without the GIL.
pub(crate) struct AttrKey(Py<PyString>);

impl AttrKey {
    pub(crate) fn new(s: Py<PyString>) -> Self {
        Self(s)
    }

    pub(crate) fn as_bound<'py>(&self, py: Python<'py>) -> Bound<'py, PyString> {
        self.0.bind(py).clone()
    }

    pub(crate) fn traverse(&self, visit: &pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        visit.call(&self.0)
    }

    fn as_str(&self) -> &str {
        // SAFETY: GIL is held by every caller (see type-level contract above).
        // `PyUnicode_AsUTF8AndSize` caches the UTF-8 encoding on the PyUnicode object;
        // subsequent calls return the same pointer without re-encoding.
        let bytes = unsafe {
            let mut size: ffi::Py_ssize_t = 0;
            let ptr = ffi::PyUnicode_AsUTF8AndSize(self.0.as_ptr(), &mut size);
            // NULL is returned for lone surrogates or allocation failure; an exception
            // is set on the interpreter. Clear it so the #[pymethods] caller doesn't
            // see a spurious UnicodeEncodeError on its next return to Python.
            let Some(ptr) = std::ptr::NonNull::new(
                // cast_mut: NonNull::new requires *mut T; no writes occur through this pointer.
                ptr.cast_mut().cast::<u8>(),
            ) else {
                ffi::PyErr_Clear();
                return "";
            };
            let Ok(len) = usize::try_from(size) else {
                return "";
            };
            std::slice::from_raw_parts(ptr.as_ptr(), len)
        };
        // CPython guarantees valid UTF-8 from a non-NULL return; this is a
        // belt-and-suspenders check in case that invariant is ever violated.
        std::str::from_utf8(bytes).unwrap_or("")
    }
}

impl Hash for AttrKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.as_str().hash(state);
    }
}

impl PartialEq for AttrKey {
    fn eq(&self, other: &Self) -> bool {
        self.as_str() == other.as_str()
    }
}

impl Eq for AttrKey {}

impl Borrow<str> for AttrKey {
    fn borrow(&self) -> &str {
        self.as_str()
    }
}

/// Typed storage for a single numeric span attribute (a `metrics` entry).
///
/// Both variants are one machine word; the enum is 16 bytes total (8-byte
/// payload + discriminant + alignment).
///
/// Bool is intentionally absent — `extract::<i64>()` succeeds for Python bool
/// (True → 1, False → 0), so bool collapses into `Int` at write time.
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
}

/// Span `meta` map: string-valued tags, keyed by attribute name.
///
/// `FxHashMap` over `std::collections::HashMap` — keys are short, trusted tag
/// names from instrumented Python code, so SipHash DoS resistance is unnecessary.
/// FxHash is faster on short keys and `FxBuildHasher` is a ZST (saves the
/// 16-byte `RandomState` per map instance).
pub(crate) type MetaMap = FxHashMap<AttrKey, Py<PyString>>;

/// Span `metrics` map: numeric-valued tags, keyed by attribute name.
/// Mutually exclusive with [`MetaMap`] — a given key lives in exactly one of
/// the two maps at a time; `SpanData`'s attribute methods enforce this.
pub(crate) type MetricsMap = FxHashMap<AttrKey, MetricValue>;
