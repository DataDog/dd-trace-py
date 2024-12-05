// taint_range.rs
use pyo3::prelude::*;
use pyo3::basic::CompareOp;
use std::hash::{Hash, Hasher};

use crate::source::Source;

#[pyclass(module = "native_rust")]
#[derive(Clone, Debug, Default)]
pub struct TaintRange {
    #[pyo3(get, set)]
    pub start: usize,
    #[pyo3(get, set)]
    pub length: usize,
    #[pyo3(get, set)]
    pub source: Source,
}

#[pymethods]
impl TaintRange {
    /// Creates a new `TaintRange` instance.
    #[new]
    #[pyo3(signature = (start=None, length=None, source=None))]
    pub fn new(
        start: Option<usize>,
        length: Option<usize>,
        source: Option<Source>,
    ) -> PyResult<Self> {
        let start = start.unwrap_or(0);
        let length = length.unwrap_or(0);
        if length == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Error: Length cannot be set to 0.",
            ));
        }
        Ok(TaintRange {
            start,
            length,
            source: source.unwrap_or_default(),
        })
    }

    /// Sets the values of the `TaintRange` instance.
    pub fn set_values(
        &mut self,
        start: usize,
        length: usize,
        source: Source,
    ) -> PyResult<()> {
        if length == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Error: Length cannot be set to 0.",
            ));
        }
        self.start = start;
        self.length = length;
        self.source = source;
        Ok(())
    }

    /// Resets the `TaintRange` instance to default values.
    pub fn reset(&mut self) {
        self.start = 0;
        self.length = 0;
        self.source.reset();
    }

    /// Returns a string representation of the `TaintRange`.
    fn __str__(&self) -> String {
        format!(
            "TaintRange(start: {}, length: {}, source: {})",
            self.start,
            self.length,
            self.source
        )
    }

    /// Representation for debugging.
    fn __repr__(&self) -> String {
        self.to_string()
    }

    /// Returns a hash value for the `TaintRange`.
    fn __hash__(&self) -> u64 {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    /// Checks equality with another `TaintRange`.
    fn __richcmp__(&self, other: &Self, op: CompareOp) -> PyResult<bool> {
        use CompareOp::*;
        let result = match op {
            Eq => self == other,
            Ne => self != other,
            _ => false,
        };
        Ok(result)
    }
}

impl Hash for TaintRange {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.start.hash(state);
        self.length.hash(state);
        self.source.hash(state);
    }
}

impl PartialEq for TaintRange {
    fn eq(&self, other: &Self) -> bool {
        self.start == other.start && self.length == other.length && self.source == other.source
    }
}

impl Eq for TaintRange {}

impl std::fmt::Display for TaintRange {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "TaintRange(start: {}, length: {}, source: {})",
            self.start,
            self.length,
            self.source
        )
    }
}

pub type TaintRangePtr = Py<TaintRange>;
pub type TaintRangeRefs = Vec<TaintRangePtr>;
