// origin_type.rs
use pyo3::prelude::*;
use pyo3::basic::CompareOp;


#[pyclass(module = "native_rust")]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OriginType {
    Parameter = 0,
    ParameterName = 1,
    Header = 2,
    HeaderName = 3,
    Path = 4,
    Body = 5,
    Query = 6,
    PathParameter = 7,
    Cookie = 8,
    CookieName = 9,
    GrpcBody = 10,
    Empty = 11,
}

impl Default for OriginType {
    fn default() -> Self {
        OriginType::Empty
    }
}

#[pymethods]
impl OriginType {
    #[staticmethod]
    pub fn from_int(value: u32) -> PyResult<Self> {
        match value {
            0 => Ok(OriginType::Parameter),
            1 => Ok(OriginType::ParameterName),
            2 => Ok(OriginType::Header),
            3 => Ok(OriginType::HeaderName),
            4 => Ok(OriginType::Path),
            5 => Ok(OriginType::Body),
            6 => Ok(OriginType::Query),
            7 => Ok(OriginType::PathParameter),
            8 => Ok(OriginType::Cookie),
            9 => Ok(OriginType::CookieName),
            10 => Ok(OriginType::GrpcBody),
            11 => Ok(OriginType::Empty),
            _ => Err(pyo3::exceptions::PyValueError::new_err("Invalid value for OriginType")),
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

