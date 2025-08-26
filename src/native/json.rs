use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use serde_json;
use std::collections::HashMap;

/// Fast JSON encoder for structured data
/// Accepts any combination of basic Python types: dict, list, tuple, str, bool, int, float, None
#[pyfunction]
pub fn dumps(py: Python, data: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    let json_value = python_to_json_value(data)?;

    // Serialize to JSON using serde_json (very fast)
    match serde_json::to_string(&json_value) {
        Ok(json_string) => Ok(json_string.into_pyobject(py)?.into_any().unbind()),
        Err(e) => Err(PyValueError::new_err(format!(
            "JSON serialization failed: {}",
            e
        ))),
    }
}

/// Convert Python value to serde_json::Value
/// Supports: None, bool, int, float, str, list, tuple, dict
fn python_to_json_value(py_value: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    // Handle None
    if py_value.is_none() {
        return Ok(serde_json::Value::Null);
    }

    // Handle bool (must come before int since bool is subtype of int in Python)
    if let Ok(b) = py_value.extract::<bool>() {
        return Ok(serde_json::Value::Bool(b));
    }

    // Handle integers
    if let Ok(i) = py_value.extract::<i64>() {
        return Ok(serde_json::Value::Number(serde_json::Number::from(i)));
    }

    // Handle floats
    if let Ok(f) = py_value.extract::<f64>() {
        if let Some(num) = serde_json::Number::from_f64(f) {
            return Ok(serde_json::Value::Number(num));
        }
    }

    // Handle strings
    if let Ok(s) = py_value.extract::<String>() {
        return Ok(serde_json::Value::String(s));
    }

    // Handle dictionaries
    if let Ok(dict) = py_value.downcast::<PyDict>() {
        let mut map: HashMap<String, serde_json::Value> = HashMap::new();
        for (key, value) in dict.iter() {
            let key_str: String = key.extract()?;
            let json_value = python_to_json_value(&value)?;
            map.insert(key_str, json_value);
        }
        return Ok(serde_json::Value::Object(serde_json::Map::from_iter(map)));
    }

    // Handle lists
    if let Ok(list) = py_value.downcast::<PyList>() {
        let mut vec: Vec<serde_json::Value> = Vec::new();
        for item in list.iter() {
            let json_value = python_to_json_value(&item)?;
            vec.push(json_value);
        }
        return Ok(serde_json::Value::Array(vec));
    }

    // Handle tuples (convert to JSON arrays)
    if let Ok(tuple) = py_value.downcast::<PyTuple>() {
        let mut vec: Vec<serde_json::Value> = Vec::new();
        for item in tuple.iter() {
            let json_value = python_to_json_value(&item)?;
            vec.push(json_value);
        }
        return Ok(serde_json::Value::Array(vec));
    }

    // Fallback: convert to string for any other type
    let s: String = py_value.str()?.extract()?;
    Ok(serde_json::Value::String(s))
}

/// Register JSON encoder functions with the Python module
pub fn register_json_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(dumps, m)?)?;
    Ok(())
}
