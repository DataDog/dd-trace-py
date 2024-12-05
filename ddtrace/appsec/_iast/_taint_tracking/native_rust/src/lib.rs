// lib.rs
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use pyo3::types::PyList;


mod origin_type;
mod tag_mapping_mode;
mod source;
mod taint_range;
mod initializer;
mod aspect_lower;
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
    m.add_function(wrap_pyfunction!(get_ranges, m)?)?;
    m.add_function(wrap_pyfunction!(set_ranges, m)?)?;
    m.add_function(wrap_pyfunction!(aspect_lower::api_lower_text, m)?)?;

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
fn get_ranges<'a>(py: Python<'a>, s: &str) -> PyResult<Option<Bound<'a, PyList>>> {
    let key = utils::calculate_hash(s);
    let initializer = initializer::get_initializer();

    if let Some(taint_ranges) = initializer.get_value(py, key) {
        let pylist = PyList::new(py, taint_ranges)?;
        Ok(Some(pylist))
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
