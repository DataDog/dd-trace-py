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
pub struct AttrKey(pub Py<PyString>);

impl AttrKey {
    pub fn new(s: Py<PyString>) -> Self {
        Self(s)
    }

    pub fn as_bound<'py>(&self, py: Python<'py>) -> Bound<'py, PyString> {
        self.0.bind(py).clone()
    }

    fn as_str_unchecked(&self) -> &str {
        // SAFETY: GIL is held by every caller (see type-level contract above).
        // `PyUnicode_AsUTF8AndSize` caches the UTF-8 encoding on the PyUnicode object;
        // subsequent calls return the same pointer without re-encoding.
        unsafe {
            let mut size: ffi::Py_ssize_t = 0;
            let ptr = ffi::PyUnicode_AsUTF8AndSize(self.0.as_ptr(), &mut size);
            if ptr.is_null() || size < 0 {
                return "";
            }
            let bytes = std::slice::from_raw_parts(ptr as *const u8, size as usize);
            // CPython guarantees PyUnicode_AsUTF8AndSize returns valid UTF-8.
            std::str::from_utf8_unchecked(bytes)
        }
    }
}

impl Hash for AttrKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.as_str_unchecked().hash(state);
    }
}

impl PartialEq for AttrKey {
    fn eq(&self, other: &Self) -> bool {
        self.as_str_unchecked() == other.as_str_unchecked()
    }
}

impl Eq for AttrKey {}

impl Borrow<str> for AttrKey {
    fn borrow(&self) -> &str {
        self.as_str_unchecked()
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
pub enum AttributeValue {
    /// A Python str, stored as a reference to the live Python object.
    Str(Py<PyString>),
    Int(i64),
    Float(f64),
}

impl AttributeValue {
    /// Return the natural Python object for this value (no v0.4 projection).
    /// Str → str, Int → int, Float → float.
    pub fn as_py<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        match self {
            AttributeValue::Str(s) => s.bind(py).clone().into_any(),
            AttributeValue::Int(i) => i.into_pyobject(py).expect("i64 into_pyobject").into_any(),
            AttributeValue::Float(f) => f.into_pyobject(py).expect("f64 into_pyobject").into_any(),
        }
    }
}

/// Span attribute map.
///
/// `FxHashMap` over `std::collections::HashMap` — keys are short, trusted tag
/// names from instrumented Python code, so SipHash DoS resistance is unnecessary.
/// FxHash is faster on short keys and `FxBuildHasher` is a ZST (saves the
/// 16-byte `RandomState` per map instance).
pub type AttributeMap = FxHashMap<AttrKey, AttributeValue>;
