use pyo3::{
    exceptions::PyValueError,
    types::{
        PyAnyMethods as _, PyDict, PyDictMethods as _, PyMapping, PyStringMethods as _, PyTuple,
    },
    Bound, IntoPyObject as _, Py, PyAny, PyResult, Python,
};

use crate::py_string::PyBackedString;
use crate::utils::flatten_key_value_vec as flatten_key_value_vec_fn;

#[pyo3::pyclass(frozen, name = "SpanLink", module = "ddtrace.internal.native._native")]
pub struct SpanLink {
    #[pyo3(get)]
    pub(crate) trace_id: u128,
    #[pyo3(get)]
    pub(crate) span_id: u64,
    // DEV: Custom getter needed because PyBackedString requires clone_ref(py).
    pub(crate) tracestate: Option<PyBackedString>,
    #[pyo3(get)]
    pub(crate) flags: Option<i64>,
    #[pyo3(get)]
    pub(crate) attributes: Py<PyDict>,
}

#[pyo3::pymethods]
impl SpanLink {
    #[getter]
    fn tracestate<'a>(&self, py: Python<'a>) -> Option<pyo3::Bound<'a, PyAny>> {
        self.tracestate.as_ref().map(|ts| ts.as_py(py))
    }

    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (trace_id, span_id, tracestate = None, flags = None, attributes = None, _skip_validation = false))]
    fn __new__(
        py: Python<'_>,
        trace_id: u128,
        span_id: u64,
        tracestate: Option<PyBackedString>,
        flags: Option<i64>,
        attributes: Option<&Bound<'_, PyAny>>,
        _skip_validation: bool,
    ) -> PyResult<Self> {
        if !_skip_validation {
            if trace_id == 0 {
                return Err(PyValueError::new_err("trace_id must be > 0. Value is 0"));
            }
            if span_id == 0 {
                return Err(PyValueError::new_err("span_id must be > 0. Value is 0"));
            }
        }
        let attributes = match attributes {
            None => PyDict::new(py).unbind(),
            Some(obj) if obj.is_none() => PyDict::new(py).unbind(),
            Some(obj) => {
                if let Ok(d) = obj.cast_exact::<PyDict>() {
                    d.clone().unbind()
                } else {
                    let dict = PyDict::new(py);
                    let mapping = obj.cast::<PyMapping>()?;
                    dict.update(mapping)?;
                    dict.unbind()
                }
            }
        };
        Ok(Self {
            trace_id,
            span_id,
            tracestate,
            flags,
            attributes,
        })
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let d = PyDict::new(py);
        d.set_item("trace_id", format!("{:032x}", self.trace_id))?;
        d.set_item("span_id", format!("{:016x}", self.span_id))?;
        let attrs_bound = self.attributes.bind(py);
        if !attrs_bound.is_empty() {
            let attrs_out = PyDict::new(py);
            for (k, v) in attrs_bound.iter() {
                for (fk, fv) in flatten_key_value_vec_fn(py, &k, &v)? {
                    // Stringify values: bools as lowercase; strings via PyBackedString
                    // (zero-copy); anything else stringified via str() then extracted
                    // as PyBackedString to keep the result as a backed string.
                    if fv.is_instance_of::<pyo3::types::PyBool>() {
                        let b: bool = fv.extract()?;
                        attrs_out.set_item(&fk, if b { "true" } else { "false" })?;
                    } else {
                        let s: PyBackedString = fv
                            .extract::<PyBackedString>()
                            .or_else(|_| fv.str()?.extract::<PyBackedString>())?;
                        attrs_out.set_item(&fk, &s)?;
                    }
                }
            }
            d.set_item("attributes", attrs_out)?;
        }
        if let Some(ref ts) = self.tracestate {
            if !ts.is_empty() {
                d.set_item("tracestate", ts.as_py(py))?;
            }
        }
        if let Some(f) = self.flags {
            d.set_item("flags", f)?;
        }
        Ok(d.unbind())
    }

    fn __eq__(&self, py: Python<'_>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        if let Ok(other) = other.cast_exact::<SpanLink>() {
            let o = other.borrow();
            Ok(self.trace_id == o.trace_id
                && self.span_id == o.span_id
                && self.tracestate == o.tracestate
                && self.flags == o.flags
                && self.attributes.bind(py).eq(o.attributes.bind(py))?)
        } else {
            Ok(false)
        }
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let attrs = self.attributes.bind(py);
        Ok(format!(
            "SpanLink(trace_id={}, span_id={}, attributes={}, tracestate={}, flags={})",
            self.trace_id,
            self.span_id,
            attrs.repr()?.to_str()?,
            self.tracestate
                .as_ref()
                .map(|s| s.as_ref() as &str)
                .unwrap_or("None"),
            self.flags.map_or("None".to_string(), |f| f.to_string()),
        ))
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        let cls = py.get_type::<SpanLink>();
        let args = PyTuple::new(
            py,
            &[
                self.trace_id.into_pyobject(py)?.into_any().unbind(),
                self.span_id.into_pyobject(py)?.into_any().unbind(),
                match &self.tracestate {
                    Some(ts) => ts.as_py(py).unbind(),
                    None => py.None(),
                },
                self.flags.into_pyobject(py)?.into_any().unbind(),
                self.attributes.clone_ref(py).into_any(),
                true.into_pyobject(py)?.to_owned().into_any().into(), // _skip_validation=True
            ],
        )?;
        Ok(PyTuple::new(py, &[cls.into_any().unbind(), args.into_any().unbind()])?.unbind())
    }
}
