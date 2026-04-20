use pyo3::{
    types::{
        PyAnyMethods as _, PyDict, PyDictMethods as _, PyList, PyMapping, PyStringMethods as _,
        PyTuple,
    },
    Bound, IntoPyObject as _, Py, PyAny, PyResult, Python,
};

use crate::py_string::PyBackedString;

use super::utils::{extract_backed_string_or_default, extract_time_unix_nano};

#[pyo3::pyclass(frozen, name = "SpanEvent", module = "ddtrace.internal.native._native")]
pub struct SpanEvent {
    #[pyo3(get)]
    pub(crate) name: PyBackedString,
    #[pyo3(get)]
    pub(crate) time_unix_nano: u64,
    #[pyo3(get)]
    pub(crate) attributes: Py<PyDict>,
}

#[pyo3::pymethods]
impl SpanEvent {
    #[new]
    #[pyo3(signature = (name, attributes = None, time_unix_nano = None))]
    fn __new__(
        py: Python<'_>,
        name: &Bound<'_, PyAny>,
        attributes: Option<&Bound<'_, PyAny>>,
        time_unix_nano: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let name = extract_backed_string_or_default(name);
        let time_unix_nano = extract_time_unix_nano(time_unix_nano);
        let attributes = match attributes {
            None => PyDict::new(py).unbind(),
            Some(obj) if obj.is_none() => PyDict::new(py).unbind(),
            Some(obj) => {
                if let Ok(d) = obj.cast_exact::<PyDict>() {
                    d.clone().unbind()
                } else {
                    // Accept any mapping by copying into a new dict
                    let dict = PyDict::new(py);
                    let mapping = obj.cast::<PyMapping>()?;
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
        let name_pair = PyTuple::new(
            py,
            [
                "name".into_pyobject(py)?.into_any().unbind(),
                self.name.as_py(py).unbind(),
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
        let cls = py.get_type::<SpanEvent>();
        let args = PyTuple::new(
            py,
            [
                self.name.as_py(py).unbind(),
                self.attributes.clone_ref(py).into_any(),
                self.time_unix_nano.into_pyobject(py)?.into_any().unbind(),
            ],
        )?;
        Ok(PyTuple::new(py, [cls.into_any().unbind(), args.into_any().unbind()])?.unbind())
    }
}
