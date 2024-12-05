// tag_mapping_mode.rs
use pyo3::prelude::*;
use pyo3::class::basic::CompareOp;


#[pyclass(module = "native_rust")]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TagMappingMode {
    Normal = 0,
    Mapper = 1,
    MapperReplace = 2,
}

impl Default for TagMappingMode {
    fn default() -> Self {
        TagMappingMode::Normal
    }
}

#[pymethods]
impl TagMappingMode {
    #[staticmethod]
    pub fn from_int(value: u32) -> PyResult<Self> {
        match value {
            0 => Ok(TagMappingMode::Normal),
            1 => Ok(TagMappingMode::Mapper),
            2 => Ok(TagMappingMode::MapperReplace),
            _ => Err(pyo3::exceptions::PyValueError::new_err("Invalid value for TagMappingMode")),
        }
    }

    fn __int__(&self) -> u32 {
        *self as u32
    }

    fn __repr__(&self) -> String {
        format!("{:?}", self)
    }

    fn __richcmp__(&self, other: &Self, op: CompareOp) -> PyResult<bool> {
        use CompareOp::*;
        let ordering = (*self as u32).cmp(&(*other as u32));
        let result = match op {
            Lt => ordering == std::cmp::Ordering::Less,
            Le => ordering != std::cmp::Ordering::Greater,
            Eq => ordering == std::cmp::Ordering::Equal,
            Ne => ordering != std::cmp::Ordering::Equal,
            Gt => ordering == std::cmp::Ordering::Greater,
            Ge => ordering != std::cmp::Ordering::Less,
        };
        Ok(result)
    }
}

