// source.rs
use pyo3::prelude::*;
use std::hash::{Hash, Hasher};
use pyo3::basic::CompareOp;

use crate::origin_type::OriginType;

#[pyclass(module = "native_rust")]
#[derive(Clone, Debug, Default)]
pub struct Source {
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub value: String,
    #[pyo3(get, set)]
    pub origin: OriginType,
}

#[pymethods]
impl Source {
    /// Creates a new `Source` instance.
    #[new]
    #[pyo3(signature = (name=None, value=None, origin=None))]
    pub fn new(
        name: Option<String>,
        value: Option<String>,
        origin: Option<OriginType>,
    ) -> Self {
        Source {
            name: name.unwrap_or_default(),
            value: value.unwrap_or_default(),
            origin: origin.unwrap_or_default(),
        }
    }

    /// Sets the values of the `Source` instance.
    #[pyo3(signature = (name=None, value=None, origin=None))]
    pub fn set_values(
        &mut self,
        name: Option<String>,
        value: Option<String>,
        origin: Option<OriginType>,
    ) {
        if let Some(n) = name {
            self.name = n;
        }
        if let Some(v) = value {
            self.value = v;
        }
        if let Some(o) = origin {
            self.origin = o;
        }
    }

    /// Resets the `Source` instance to default values.
    pub fn reset(&mut self) {
        self.name.clear();
        self.value.clear();
        self.origin = OriginType::Empty;
    }

    /// Returns a string representation of the `Source`.
    fn __str__(&self) -> String {
        format!(
            "Source(name: {}, value: {}, origin: {:?})",
            self.name, self.value, self.origin
        )
    }

    /// Representation for debugging.
    fn __repr__(&self) -> String {
        self.to_string()
    }

    /// Returns a hash value for the `Source`.
    fn __hash__(&self) -> u64 {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        self.hash(&mut hasher);
        hasher.finish()
    }

    /// Checks equality with another `Source`.
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

impl Hash for Source {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.name.hash(state);
        self.value.hash(state);
        self.origin.hash(state);
    }
}

impl PartialEq for Source {
    fn eq(&self, other: &Self) -> bool {
        self.name == other.name && self.value == other.value && self.origin == other.origin
    }
}

impl Eq for Source {}

impl std::fmt::Display for Source {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Source(name: {}, value: {}, origin: {:?})",
            self.name, self.value, self.origin
        )
    }
}
