use pyo3::{
    exceptions::PyKeyError,
    types::{
        PyAnyMethods as _, PyBool, PyDict, PyDictMethods as _, PyFloat, PyInt, PyList,
        PyListMethods as _, PyTuple,
    },
    Bound, IntoPyObject, Py, PyAny, PyResult, Python,
};
use std::collections::HashMap;
use std::ops::Deref;

use crate::py_string::{PyBackedString, PyTraceData};
use crate::span::{extract_backed_string_or_default, wall_clock_ns};
use libdd_trace_utils::span::v04::{
    AttributeAnyValue as LibAttributeAnyValue, AttributeArrayValue as LibAttributeArrayValue,
    SpanEvent as LibSpanEvent,
};

/// Try to get a string from a Python object:
/// 1. If it's already a str, extract directly
/// 2. Otherwise, call Python's str() to convert
/// 3. Return Err if str() itself fails
fn try_stringify(obj: &Bound<'_, PyAny>) -> PyResult<PyBackedString> {
    // Fast path: already a string
    if let Ok(s) = obj.extract::<PyBackedString>() {
        return Ok(s);
    }
    // Slow path: call str(obj)
    let py_str = obj.str()?;
    py_str.extract::<PyBackedString>()
}

// bool must be checked before int (Python bool subclasses int).
fn py_to_array_value(obj: &Bound<'_, PyAny>) -> PyResult<LibAttributeArrayValue<PyTraceData>> {
    if obj.is_instance_of::<PyBool>() {
        let b: bool = obj.extract()?;
        return Ok(LibAttributeArrayValue::Boolean(b));
    }
    if let Ok(s) = obj.extract::<PyBackedString>() {
        return Ok(LibAttributeArrayValue::String(s));
    }
    if obj.is_instance_of::<PyInt>() {
        let i: i64 = obj.extract()?;
        return Ok(LibAttributeArrayValue::Integer(i));
    }
    if obj.is_instance_of::<PyFloat>() {
        let f: f64 = obj.extract()?;
        return Ok(LibAttributeArrayValue::Double(f));
    }
    Ok(LibAttributeArrayValue::String(
        try_stringify(obj).unwrap_or_default(),
    ))
}

fn py_to_attribute_value(obj: &Bound<'_, PyAny>) -> PyResult<LibAttributeAnyValue<PyTraceData>> {
    if obj.is_instance_of::<PyBool>() {
        let b: bool = obj.extract()?;
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::Boolean(b),
        ));
    }
    if let Ok(s) = obj.extract::<PyBackedString>() {
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::String(s),
        ));
    }
    if obj.is_instance_of::<PyInt>() {
        let i: i64 = obj.extract()?;
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::Integer(i),
        ));
    }
    if obj.is_instance_of::<PyFloat>() {
        let f: f64 = obj.extract()?;
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::Double(f),
        ));
    }
    if let Ok(list) = obj.cast::<PyList>() {
        let mut values = Vec::with_capacity(list.len());
        for item in list.iter() {
            values.push(py_to_array_value(&item)?);
        }
        return Ok(LibAttributeAnyValue::Array(values));
    }
    Ok(LibAttributeAnyValue::SingleValue(
        LibAttributeArrayValue::String(try_stringify(obj).unwrap_or_default()),
    ))
}

fn py_dict_to_attributes(
    dict: &Bound<'_, PyDict>,
) -> PyResult<HashMap<PyBackedString, LibAttributeAnyValue<PyTraceData>>> {
    let mut result = HashMap::with_capacity(dict.len());
    for (k, v) in dict.iter() {
        let key = try_stringify(&k).unwrap_or_default();
        let val = py_to_attribute_value(&v)?;
        result.insert(key, val);
    }
    Ok(result)
}

fn array_value_to_py(
    py: Python<'_>,
    val: &LibAttributeArrayValue<PyTraceData>,
) -> PyResult<Py<PyAny>> {
    match val {
        LibAttributeArrayValue::String(s) => Ok(s.as_py(py).unbind()),
        LibAttributeArrayValue::Boolean(b) => Ok((*pyo3::types::PyBool::new(py, *b))
            .clone()
            .into_any()
            .unbind()),
        LibAttributeArrayValue::Integer(i) => Ok(i.into_pyobject(py)?.into_any().unbind()),
        LibAttributeArrayValue::Double(f) => Ok(f.into_pyobject(py)?.into_any().unbind()),
    }
}

fn attribute_value_to_py(
    py: Python<'_>,
    val: &LibAttributeAnyValue<PyTraceData>,
) -> PyResult<Py<PyAny>> {
    match val {
        LibAttributeAnyValue::SingleValue(arr_val) => array_value_to_py(py, arr_val),
        LibAttributeAnyValue::Array(vec) => {
            let items: PyResult<Vec<Py<PyAny>>> =
                vec.iter().map(|v| array_value_to_py(py, v)).collect();
            Ok(PyList::new(py, items?)?
                .into_pyobject(py)?
                .into_any()
                .unbind())
        }
    }
}

fn attribute_debug_repr(val: &LibAttributeAnyValue<PyTraceData>) -> String {
    match val {
        LibAttributeAnyValue::SingleValue(arr_val) => array_debug_repr(arr_val),
        LibAttributeAnyValue::Array(vec) => {
            let items: Vec<String> = vec.iter().map(array_debug_repr).collect();
            format!("[{}]", items.join(", "))
        }
    }
}

fn array_debug_repr(val: &LibAttributeArrayValue<PyTraceData>) -> String {
    match val {
        LibAttributeArrayValue::String(s) => format!("'{}'", s.deref()),
        LibAttributeArrayValue::Boolean(b) => {
            if *b {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        }
        LibAttributeArrayValue::Integer(i) => format!("{}", i),
        LibAttributeArrayValue::Double(f) => format!("{}", f),
    }
}

#[pyo3::pyclass(name = "SpanEvent", module = "ddtrace.internal._native")]
pub struct SpanEvent {
    pub(crate) data: LibSpanEvent<PyTraceData>,
}

#[pyo3::pymethods]
impl SpanEvent {
    #[new]
    #[pyo3(signature = (name, attributes = None, time_unix_nano = None))]
    pub fn __new__(
        name: &Bound<'_, PyAny>,
        attributes: Option<&Bound<'_, PyDict>>,
        time_unix_nano: Option<u64>,
    ) -> PyResult<Self> {
        let data_name = extract_backed_string_or_default(name);
        let data_time = time_unix_nano.unwrap_or_else(|| wall_clock_ns() as u64);
        let data_attributes = match attributes {
            None => HashMap::new(),
            Some(dict) => py_dict_to_attributes(dict)?,
        };
        Ok(Self {
            data: LibSpanEvent {
                name: data_name,
                time_unix_nano: data_time,
                attributes: data_attributes,
            },
        })
    }

    #[getter]
    #[inline(always)]
    fn get_name<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        self.data.name.as_py(py)
    }

    #[setter]
    #[inline(always)]
    fn set_name(&mut self, name: &Bound<'_, PyAny>) {
        self.data.name = extract_backed_string_or_default(name);
    }

    #[getter]
    #[inline(always)]
    fn get_time_unix_nano(&self) -> u64 {
        self.data.time_unix_nano
    }

    #[setter]
    #[inline(always)]
    fn set_time_unix_nano(&mut self, value: u64) {
        self.data.time_unix_nano = value;
    }

    #[getter]
    fn get_attributes(slf: Bound<'_, SpanEvent>) -> PyResult<Py<SpanEventAttributes>> {
        let py = slf.py();
        let parent = slf.unbind();
        Py::new(py, SpanEventAttributes { parent })
    }

    #[setter]
    fn set_attributes(&mut self, attributes: Option<&Bound<'_, PyDict>>) -> PyResult<()> {
        self.data.attributes = match attributes {
            None => HashMap::new(),
            Some(dict) => py_dict_to_attributes(dict)?,
        };
        Ok(())
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("name", self.data.name.as_py(py))?;
        dict.set_item("time_unix_nano", self.data.time_unix_nano)?;
        if !self.data.attributes.is_empty() {
            let attrs_dict = PyDict::new(py);
            for (k, v) in &self.data.attributes {
                attrs_dict.set_item(k.as_py(py), attribute_value_to_py(py, v)?)?;
            }
            dict.set_item("attributes", attrs_dict)?;
        }
        Ok(dict.into_any().unbind())
    }

    pub fn __repr__(&self) -> String {
        let attrs_repr = if self.data.attributes.is_empty() {
            "{}".to_owned()
        } else {
            let parts: Vec<String> = self
                .data
                .attributes
                .iter()
                .map(|(k, v)| format!("'{}': {}", k.deref(), attribute_debug_repr(v)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        };
        format!(
            "SpanEvent(name='{}', time={}, attributes={})",
            self.data.name.deref(),
            self.data.time_unix_nano,
            attrs_repr
        )
    }

    pub fn __iter__(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let name_tuple = PyTuple::new(
            py,
            [
                "name".into_pyobject(py)?.into_any(),
                self.data.name.as_py(py),
            ],
        )?;
        let time_tuple = PyTuple::new(
            py,
            [
                "time_unix_nano".into_pyobject(py)?.into_any(),
                self.data.time_unix_nano.into_pyobject(py)?.into_any(),
            ],
        )?;

        if self.data.attributes.is_empty() {
            let py_list = PyList::new(py, [name_tuple, time_tuple])?;
            return Ok(py_list.into_any().try_iter()?.unbind().into_any());
        }

        let attrs_dict = PyDict::new(py);
        for (k, v) in &self.data.attributes {
            attrs_dict.set_item(k.as_py(py), attribute_value_to_py(py, v)?)?;
        }
        let attrs_tuple = PyTuple::new(
            py,
            [
                "attributes".into_pyobject(py)?.into_any(),
                attrs_dict.into_any(),
            ],
        )?;
        let py_list = PyList::new(py, [name_tuple, time_tuple, attrs_tuple])?;
        Ok(py_list.into_any().try_iter()?.unbind().into_any())
    }
}

#[pyo3::pyclass(
    name = "SpanEventAttributes",
    module = "ddtrace.internal._native",
    mapping,
    frozen
)]
pub struct SpanEventAttributes {
    parent: Py<SpanEvent>,
}

#[pyo3::pymethods]
impl SpanEventAttributes {
    fn __len__(&self, py: Python<'_>) -> usize {
        self.parent.borrow(py).data.attributes.len()
    }

    fn __getitem__(&self, py: Python<'_>, key: &str) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        match parent.data.attributes.get(key) {
            Some(val) => attribute_value_to_py(py, val),
            None => Err(PyKeyError::new_err(key.to_owned())),
        }
    }

    fn __contains__(&self, py: Python<'_>, key: &str) -> bool {
        self.parent.borrow(py).data.attributes.contains_key(key)
    }

    fn __bool__(&self, py: Python<'_>) -> bool {
        !self.parent.borrow(py).data.attributes.is_empty()
    }

    fn __iter__(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let keys: Vec<Py<PyAny>> = parent
            .data
            .attributes
            .keys()
            .map(|k| k.as_py(py).unbind())
            .collect();
        drop(parent);
        let py_list = PyList::new(py, keys)?;
        Ok(py_list.into_any().try_iter()?.unbind().into_any())
    }

    fn keys(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let keys: Vec<Py<PyAny>> = parent
            .data
            .attributes
            .keys()
            .map(|k| k.as_py(py).unbind())
            .collect();
        drop(parent);
        Ok(PyList::new(py, keys)?.into_any().unbind())
    }

    fn values(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let values: PyResult<Vec<Py<PyAny>>> = parent
            .data
            .attributes
            .values()
            .map(|v| attribute_value_to_py(py, v))
            .collect();
        drop(parent);
        Ok(PyList::new(py, values?)?.into_any().unbind())
    }

    fn items(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let mut tuples: Vec<Py<PyAny>> = Vec::with_capacity(parent.data.attributes.len());
        for (k, v) in parent.data.attributes.iter() {
            let key = k.as_py(py).unbind();
            let val = attribute_value_to_py(py, v)?;
            let tup = PyTuple::new(py, [key, val])?;
            tuples.push(tup.into_any().unbind());
        }
        drop(parent);
        Ok(PyList::new(py, tuples)?.into_any().unbind())
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let parent = self.parent.borrow(py);
        let parts: Vec<String> = parent
            .data
            .attributes
            .iter()
            .map(|(k, v)| format!("'{}': {}", k.deref(), attribute_debug_repr(v)))
            .collect();
        format!("{{{}}}", parts.join(", "))
    }

    fn __eq__(&self, py: Python<'_>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        let parent = self.parent.borrow(py);
        let dict = PyDict::new(py);
        for (k, v) in parent.data.attributes.iter() {
            dict.set_item(k.as_py(py), attribute_value_to_py(py, v)?)?;
        }
        drop(parent);
        dict.into_any().eq(other)
    }
}
