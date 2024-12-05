// lib.rs
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use pyo3::types::PyList;


mod origin_type;
mod tag_mapping_mode;
mod source;
mod taint_range;
mod initializer;
mod utils;

pub use origin_type::OriginType;
pub use tag_mapping_mode::TagMappingMode;
pub use source::Source;
pub use taint_range::TaintRange;

use crate::taint_range::TaintRangeRefs;

#[pymodule]
fn native_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Initialize the singleton `Initializer`
    initializer::initialize();

    // Expose functions to Python
    m.add_function(wrap_pyfunction!(reset_context_py, m)?)?;
    m.add_function(wrap_pyfunction!(get_ranges_py, m)?)?;
    m.add_function(wrap_pyfunction!(set_ranges, m)?)?;

    // Add your classes to the module
    m.add_class::<OriginType>()?;
    m.add_class::<TagMappingMode>()?;
    m.add_class::<Source>()?;
    m.add_class::<TaintRange>()?;
    Ok(())
}

#[pyfunction]
fn reset_context_py() {
    initializer::get_initializer().reset_context();
}

#[pyfunction]
fn get_ranges_py(py: Python, s: &str) -> PyResult<Option<PyObject>> {
    let key = utils::calculate_hash(s);
    let initializer = initializer::get_initializer();

    if let Some(taint_ranges) = initializer.get_value(py, key) {
        // Convert taint_ranges (Vec<Py<TaintRange>>) into a Python list
        let pylist = PyList::new(py, taint_ranges)?; // Handle potential error
        Ok(Some(pylist.into()))               // Convert to PyObject
    } else {
        Ok(None)
    }
}

#[pyfunction]
fn set_ranges(py: Python, s: &str, ranges: &Bound<'_, PyList>) -> PyResult<()> {
    let ranges_vec: Vec<TaintRange> = ranges
        .iter()
        .map(|item| item.extract())
        .collect::<PyResult<_>>()?;
    let taint_range_refs: TaintRangeRefs = ranges_vec.into_iter().map(|t| Py::new(py, t).unwrap()).collect();
    initializer::get_initializer().store_taint_ranges_for_string(s, taint_range_refs);
    Ok(())
}
