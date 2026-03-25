use pyo3::{
    exceptions::PyValueError,
    types::{
        PyAnyMethods as _, PyDict, PyDictMethods as _, PyMapping, PyStringMethods as _, PyTuple,
        PyType,
    },
    Bound, IntoPyObject as _, Py, PyAny, PyResult, Python,
};

use crate::utils::flatten_key_value as flatten_key_value_fn;

#[pyo3::pyclass(frozen, name = "SpanLink", module = "ddtrace.internal.native._native")]
pub struct SpanLink {
    #[pyo3(get)]
    trace_id: u128,
    #[pyo3(get)]
    span_id: u64,
    #[pyo3(get)]
    tracestate: Option<String>,
    #[pyo3(get)]
    flags: Option<i64>,
    #[pyo3(get)]
    attributes: Py<PyDict>,
    #[pyo3(get)]
    _dropped_attributes: u32,
}

#[pyo3::pymethods]
impl SpanLink {
    #[classattr]
    const SPAN_POINTER_KIND: &'static str = "span-pointer";

    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (trace_id, span_id, tracestate = None, flags = None, attributes = None, _dropped_attributes = 0, _skip_validation = false))]
    fn __new__(
        py: Python<'_>,
        trace_id: u128,
        span_id: u64,
        tracestate: Option<String>,
        flags: Option<i64>,
        attributes: Option<&Bound<'_, PyAny>>,
        _dropped_attributes: u32,
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
            _dropped_attributes,
        })
    }

    #[getter]
    fn name(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = self.attributes.bind(py);
        match dict.get_item("link.name")? {
            Some(v) => Ok(v.unbind()),
            None => Err(pyo3::exceptions::PyKeyError::new_err("link.name")),
        }
    }

    #[getter]
    fn kind(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let dict = self.attributes.bind(py);
        Ok(dict.get_item("link.kind")?.map(|v| v.unbind()))
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let d = PyDict::new(py);
        d.set_item("trace_id", format!("{:032x}", self.trace_id))?;
        d.set_item("span_id", format!("{:016x}", self.span_id))?;
        let attrs_bound = self.attributes.bind(py);
        if !attrs_bound.is_empty() {
            let attrs_out = PyDict::new(py);
            for (k, v) in attrs_bound.iter() {
                let flattened = flatten_key_value_fn(py, k.str()?.to_str()?, &v)?;
                for (k1, v1) in flattened.bind(py).iter() {
                    // stringify: bools as lowercase, others as str()
                    let s = if v1.is_instance_of::<pyo3::types::PyBool>() {
                        let b: bool = v1.extract()?;
                        if b {
                            "true".to_string()
                        } else {
                            "false".to_string()
                        }
                    } else if let Ok(s) = v1.extract::<String>() {
                        s
                    } else {
                        v1.str()?.to_str()?.to_string()
                    };
                    attrs_out.set_item(k1, s)?;
                }
            }
            d.set_item("attributes", attrs_out)?;
        }
        if self._dropped_attributes > 0 {
            d.set_item("dropped_attributes_count", self._dropped_attributes)?;
        }
        if let Some(ref ts) = self.tracestate {
            if !ts.is_empty() {
                d.set_item("tracestate", ts)?;
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
                && self._dropped_attributes == o._dropped_attributes
                && self.attributes.bind(py).eq(o.attributes.bind(py))?)
        } else {
            Ok(false)
        }
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let attrs = self.attributes.bind(py);
        Ok(format!(
            "SpanLink(trace_id={}, span_id={}, attributes={}, tracestate={}, flags={}, dropped_attributes={})",
            self.trace_id,
            self.span_id,
            attrs.repr()?.to_str()?,
            self.tracestate.as_deref().unwrap_or("None"),
            self.flags.map_or("None".to_string(), |f| f.to_string()),
            self._dropped_attributes,
        ))
    }

    fn __reduce__(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        let cls = py.get_type::<SpanLink>();
        let args = PyTuple::new(
            py,
            &[
                self.trace_id.into_pyobject(py)?.into_any().unbind(),
                self.span_id.into_pyobject(py)?.into_any().unbind(),
                self.tracestate
                    .clone()
                    .into_pyobject(py)?
                    .into_any()
                    .unbind(),
                self.flags.into_pyobject(py)?.into_any().unbind(),
                self.attributes.clone_ref(py).into_any(),
                self._dropped_attributes
                    .into_pyobject(py)?
                    .into_any()
                    .unbind(),
                <pyo3::Bound<'_, pyo3::types::PyBool> as Clone>::clone(&pyo3::types::PyBool::new(
                    py, true,
                ))
                .unbind()
                .into_any(), // _skip_validation=True
            ],
        )?;
        Ok(PyTuple::new(py, &[cls.into_any().unbind(), args.into_any().unbind()])?.unbind())
    }

    #[classmethod]
    #[pyo3(signature = (pointer_kind, pointer_direction, pointer_hash, extra_attributes = None))]
    #[allow(non_snake_case)]
    fn _SpanPointer(
        _cls: &Bound<'_, PyType>,
        py: Python<'_>,
        pointer_kind: String,
        pointer_direction: &Bound<'_, PyAny>,
        pointer_hash: String,
        extra_attributes: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<SpanLink>> {
        // Extract direction value: if it has a .value attr (enum), use that, otherwise use as string
        let direction_val: String = if let Ok(v) = pointer_direction.getattr("value") {
            v.extract()?
        } else {
            pointer_direction.extract()?
        };

        let attrs = PyDict::new(py);
        // Set extra_attributes first so they cannot override the required ptr.* keys
        if let Some(extra) = extra_attributes {
            attrs.update(extra.as_mapping())?;
        }
        attrs.set_item("ptr.kind", &pointer_kind)?;
        attrs.set_item("ptr.dir", &direction_val)?;
        attrs.set_item("ptr.hash", &pointer_hash)?;
        attrs.set_item("link.kind", Self::SPAN_POINTER_KIND)?;

        let link = SpanLink {
            trace_id: 0,
            span_id: 0,
            tracestate: None,
            flags: None,
            attributes: attrs.unbind(),
            _dropped_attributes: 0,
        };
        Py::new(py, link)
    }
}

