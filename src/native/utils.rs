use pyo3::{
    types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyFrozenSet, PyList, PySet, PyTuple},
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

/// Python-facing flatten_key_value (registered as pyfunction)
#[pyo3::pyfunction]
pub fn flatten_key_value(py: Python<'_>, root_key: &str, value: &Bound<'_, PyAny>) -> PyResult<pyo3::Py<PyDict>> {
    let result = PyDict::new(py);
    flatten_into(py, &result, root_key, value)?;
    Ok(result.unbind())
}

fn flatten_into(
    py: Python<'_>,
    out: &Bound<'_, PyDict>,
    root_key: &str,
    value: &Bound<'_, PyAny>,
) -> PyResult<()> {
    if !is_sequence_py(value) {
        out.set_item(root_key, value)?;
        return Ok(());
    }
    use pyo3::types::{PyIterator, PyListMethods as _};
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
        let key = format!("{}.{}", root_key, i);
        if is_sequence_py(item) {
            flatten_into(py, out, &key, item)?;
        } else {
            out.set_item(key, item)?;
        }
    }
    Ok(())
}
