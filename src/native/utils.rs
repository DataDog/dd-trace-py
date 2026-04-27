use pyo3::{
    types::{
        PyAnyMethods as _, PyDict, PyDictMethods as _, PyFrozenSet, PyList, PySet, PyString,
        PyStringMethods as _, PyTuple,
    },
    Bound, PyAny, PyResult, Python,
};

/// Inline helper: matches Python's is_sequence (list, tuple, set, frozenset)
#[inline]
pub fn is_sequence_py(obj: &Bound<'_, PyAny>) -> bool {
    obj.is_instance_of::<PyList>()
        || obj.is_instance_of::<PyTuple>()
        || obj.is_instance_of::<PySet>()
        || obj.is_instance_of::<PyFrozenSet>()
}

/// Python-facing is_sequence (registered as pyfunction)
#[pyo3::pyfunction]
pub fn is_sequence(obj: &Bound<'_, PyAny>) -> bool {
    is_sequence_py(obj)
}

/// Internal Rust-facing flatten: returns pairs of `(Bound<PyAny>, Bound<PyAny>)` where keys
/// are Python string objects. This avoids copies: for non-sequence values the original
/// root_key Python object is returned directly; for composed keys a new PyString is created.
pub fn flatten_key_value_vec<'py>(
    py: Python<'py>,
    root_key: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
) -> PyResult<Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>> {
    let mut out = Vec::new();
    flatten_into_vec(py, &mut out, root_key, value)?;
    Ok(out)
}

/// Python-facing flatten_key_value (registered as pyfunction).
/// Thin wrapper over `flatten_key_value_vec` that converts the result to a PyDict.
#[pyo3::pyfunction]
pub fn flatten_key_value(
    py: Python<'_>,
    root_key: &str,
    value: &Bound<'_, PyAny>,
) -> PyResult<pyo3::Py<PyDict>> {
    let root_key_py = PyString::new(py, root_key).into_any();
    let pairs = flatten_key_value_vec(py, &root_key_py, value)?;
    let result = PyDict::new(py);
    for (k, v) in pairs {
        result.set_item(k, v)?;
    }
    Ok(result.unbind())
}

fn flatten_into_vec<'py>(
    py: Python<'py>,
    out: &mut Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>,
    root_key: &Bound<'py, PyAny>,
    value: &Bound<'py, PyAny>,
) -> PyResult<()> {
    if !is_sequence_py(value) {
        out.push((root_key.clone(), value.clone()));
        return Ok(());
    }
    use pyo3::types::{PyIterator, PyListMethods as _};
    // Extract root_key as &str for composing child keys
    let root_py_str = root_key.str()?;
    let root_str = root_py_str.to_str()?;
    let items: Vec<pyo3::Py<PyAny>> = if let Ok(list) = value.cast_exact::<PyList>() {
        list.iter().map(|item| item.unbind()).collect()
    } else {
        let iter = PyIterator::from_object(value)?;
        let mut v = Vec::new();
        for item in iter {
            v.push(item?.unbind());
        }
        v
    };
    for (i, item) in items.iter().enumerate() {
        let item = item.bind(py);
        let child_key = PyString::new(py, &format!("{}.{}", root_str, i)).into_any();
        if is_sequence_py(item) {
            flatten_into_vec(py, out, &child_key, item)?;
        } else {
            out.push((child_key, item.clone()));
        }
    }
    Ok(())
}
