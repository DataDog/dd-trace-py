use pyo3::pymodule;

#[pymodule(module = "appsec")]
pub mod libddwaf {
    use std::ffi::CStr;

    use pyo3::pyfunction;

    #[pyfunction]
    fn ddwaf_get_version() -> &'static CStr {
        ::libddwaf::version()
    }
}
