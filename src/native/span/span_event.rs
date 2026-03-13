use pyo3::{
    types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyList, PyMapping, PyStringMethods as _, PyTuple},
    Bound, IntoPyObject as _, Py, PyAny, PyResult, Python,
};

use crate::py_string::PyBackedString;

use super::utils::{extract_backed_string_or_default, wall_clock_ns};

#[pyo3::pyclass(frozen, name = "SpanEvent", module = "ddtrace.internal.native._native")]
pub struct SpanEvent {
    #[pyo3(get)]
    name: PyBackedString,
    #[pyo3(get)]
    time_unix_nano: i64,
    #[pyo3(get)]
    attributes: Py<PyDict>,
}

#[pyo3::pymethods]
impl SpanEvent {
    #[new]
    #[pyo3(signature = (name, attributes = None, time_unix_nano = None))]
    fn __new__(
        py: Python<'_>,
        name: &Bound<'_, PyAny>,
        attributes: Option<&Bound<'_, PyAny>>,
        time_unix_nano: Option<i64>,
    ) -> PyResult<Self> {
        let name = extract_backed_string_or_default(name);
        let time_unix_nano = time_unix_nano.unwrap_or_else(wall_clock_ns);
        let attributes = match attributes {
            None => PyDict::new(py).unbind(),
            Some(obj) if obj.is_none() => PyDict::new(py).unbind(),
            Some(obj) => {
                if let Ok(d) = obj.downcast::<PyDict>() {
                    d.clone().unbind()
                } else {
                    // Accept any mapping by copying into a new dict
                    let dict = PyDict::new(py);
                    let mapping = obj.downcast::<PyMapping>()?;
                    dict.update(mapping)?;
                    dict.unbind()
                }
            }
        };
        Ok(Self {
            name,
            time_unix_nano,
            attributes,
        })
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let name_str: &str = &self.name;
        let attrs_repr = self.attributes.bind(py).repr()?;
        Ok(format!(
            "SpanEvent(name='{}', time={}, attributes={})",
            name_str,
            self.time_unix_nano,
            attrs_repr.to_str()?
        ))
    }

    fn __iter__(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let name_str: &str = &self.name;
        let name_pair = PyTuple::new(
            py,
            [
                "name".into_pyobject(py)?.into_any().unbind(),
                name_str.into_pyobject(py)?.into_any().unbind(),
            ],
        )?;
        let time_pair = PyTuple::new(
            py,
            [
                "time_unix_nano".into_pyobject(py)?.into_any().unbind(),
                self.time_unix_nano.into_pyobject(py)?.into_any().unbind(),
            ],
        )?;
        let mut items: Vec<Py<PyAny>> =
            vec![name_pair.into_any().unbind(), time_pair.into_any().unbind()];
        let attrs = self.attributes.bind(py);
        if !attrs.is_empty() {
            let attrs_pair = PyTuple::new(
                py,
                [
                    "attributes".into_pyobject(py)?.into_any().unbind(),
                    attrs.clone().into_any().unbind(),
                ],
            )?;
            items.push(attrs_pair.into_any().unbind());
        }
        let list = PyList::new(py, items)?;
        let iter = list.call_method0("__iter__")?;
        Ok(iter.unbind())
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        let name_str: &str = &self.name;
        let cls = py.get_type::<SpanEvent>();
        let args = PyTuple::new(
            py,
            [
                name_str.into_pyobject(py)?.into_any().unbind(),
                self.attributes.clone_ref(py).into_any(),
                self.time_unix_nano.into_pyobject(py)?.into_any().unbind(),
            ],
        )?;
        Ok(PyTuple::new(py, [cls.into_any().unbind(), args.into_any().unbind()])?.unbind())
    }
}
