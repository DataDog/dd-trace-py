use pyo3::{
    prelude::*,
    types::{PyDict, PyList, PyTuple},
    PyTraverseError, PyVisit,
};
use std::collections::HashMap;
use std::sync::{LazyLock, OnceLock, RwLock};

type Listeners = HashMap<String, Vec<(Py<PyAny>, Py<PyAny>)>>;

// (name_key, callback) pairs per event_id
static LISTENERS: LazyLock<RwLock<Listeners>> = LazyLock::new(|| RwLock::new(HashMap::new()));

// Cached Python objects — initialized on first use, never invalidated.
// The losing thread in a race drops its Py<PyAny>; pyo3 0.28 defers the decref.
static MISSING_EVENT: OnceLock<Py<PyAny>> = OnceLock::new();
static MISSING_EVENT_DICT: OnceLock<Py<PyAny>> = OnceLock::new();
// ResultType::Ok/Exception interned once so dispatch_with_results avoids per-listener allocation.
static RT_OK_PY: OnceLock<Py<PyAny>> = OnceLock::new();
static RT_EXCEPTION_PY: OnceLock<Py<PyAny>> = OnceLock::new();

// OnceLock::get_or_try_init is unstable; use get+set pattern instead.
macro_rules! get_or_init {
    ($cell:expr, $py:expr, $init:expr) => {{
        if let Some(val) = $cell.get() {
            Ok(val)
        } else {
            let val: PyResult<Py<PyAny>> = $init;
            match val {
                Ok(v) => {
                    let _ = $cell.set(v);
                    Ok($cell.get().unwrap())
                }
                Err(e) => Err(e),
            }
        }
    }};
}

/// Native equivalent of the Python ResultType enum.
/// pyo3 automatically exposes each variant as a class attribute.
#[pyclass(
    eq,
    hash,
    frozen,
    from_py_object,
    module = "ddtrace.internal.native._native"
)]
#[derive(Clone, PartialEq, Eq, Hash)]
pub enum ResultType {
    #[pyo3(name = "RESULT_OK")]
    Ok = 0,
    #[pyo3(name = "RESULT_EXCEPTION")]
    Exception = 1,
    #[pyo3(name = "RESULT_UNDEFINED")]
    Undefined = -1,
}

#[pymethods]
impl ResultType {
    #[getter]
    fn value(&self) -> i32 {
        match self {
            ResultType::Ok => 0,
            ResultType::Exception => 1,
            ResultType::Undefined => -1,
        }
    }

    #[getter]
    fn name(&self) -> &'static str {
        match self {
            ResultType::Ok => "RESULT_OK",
            ResultType::Exception => "RESULT_EXCEPTION",
            ResultType::Undefined => "RESULT_UNDEFINED",
        }
    }

    fn __repr__(&self) -> String {
        format!("<ResultType.{}: {}>", self.name(), self.value())
    }

    fn __int__(&self) -> i32 {
        self.value()
    }
}

fn get_missing_event(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(MISSING_EVENT, py, {
        let result_type_undefined = ResultType::Undefined.into_pyobject(py)?.into_any().unbind();
        let event_result = Py::new(
            py,
            EventResult {
                response_type: Some(result_type_undefined),
                value: None,
                exception: None,
                is_ok: false,
            },
        )?;
        Ok(event_result.into_any())
    })
}

fn get_missing_event_dict(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(MISSING_EVENT_DICT, py, {
        Ok(Py::new(py, EventResultDict)?.into_any())
    })
}

fn get_rt_ok(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(RT_OK_PY, py, {
        Ok(ResultType::Ok.into_pyobject(py)?.into_any().unbind())
    })
}

fn get_rt_exception(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(RT_EXCEPTION_PY, py, {
        Ok(ResultType::Exception.into_pyobject(py)?.into_any().unbind())
    })
}

fn repr_field(py: Python<'_>, field: &Option<Py<PyAny>>) -> PyResult<String> {
    Ok(match field {
        Some(o) => o.bind(py).repr()?.to_string(),
        None => "None".to_string(),
    })
}

/// Native Python class mirroring the old Python EventResult dataclass.
/// Fields are Option<Py<PyAny>> so __clear__ can drop them without a Python token —
/// pyo3 0.28 defers Py<T> decrefs, so dropping Option::None is always safe.
/// is_ok is a private fast path for __bool__ that avoids Python equality.
/// response_type is get-only to keep is_ok consistent; value and exception are mutable.
#[pyclass(module = "ddtrace.internal.native._native")]
pub struct EventResult {
    #[pyo3(get)]
    pub response_type: Option<Py<PyAny>>,
    #[pyo3(get, set)]
    pub value: Option<Py<PyAny>>,
    #[pyo3(get, set)]
    pub exception: Option<Py<PyAny>>,
    is_ok: bool,
}

#[pymethods]
impl EventResult {
    #[new]
    #[pyo3(signature = (response_type=None, value=None, exception=None))]
    fn py_new(
        py: Python<'_>,
        response_type: Option<Py<PyAny>>,
        value: Option<Py<PyAny>>,
        exception: Option<Py<PyAny>>,
    ) -> Self {
        let is_ok = response_type
            .as_ref()
            .and_then(|rt| rt.extract::<ResultType>(py).ok())
            .map(|rt| rt == ResultType::Ok)
            .unwrap_or(false);
        Self {
            response_type: Some(response_type.unwrap_or_else(|| py.None().into_any())),
            value: Some(value.unwrap_or_else(|| py.None().into_any())),
            exception: Some(exception.unwrap_or_else(|| py.None().into_any())),
            is_ok,
        }
    }

    fn __bool__(&self) -> bool {
        self.is_ok
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let rt = repr_field(py, &self.response_type)?;
        let val = repr_field(py, &self.value)?;
        let exc = repr_field(py, &self.exception)?;
        Ok(format!(
            "EventResult(response_type={rt}, value={val}, exception={exc})"
        ))
    }

    // __traverse__ registers this class with Python's cyclic GC. In pyo3 0.28
    // defining this method is sufficient — no #[pyclass(gc)] needed.
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Some(ref o) = self.response_type {
            visit.call(o)?;
        }
        if let Some(ref o) = self.value {
            visit.call(o)?;
        }
        if let Some(ref o) = self.exception {
            visit.call(o)?;
        }
        Ok(())
    }

    // __clear__ breaks reference cycles. Option fields can be taken without
    // acquiring the GIL — pyo3 0.28 defers the decref automatically.
    fn __clear__(&mut self) {
        self.response_type = None;
        self.value = None;
        self.exception = None;
        self.is_ok = false;
    }
}

/// Native dict subclass mirroring the old Python EventResultDict.
/// __missing__ returns the _MissingEvent singleton for absent keys.
/// __getattr__ enables attribute-style access: result.listener_name.
/// GC is inherited from PyDict — no extra fields to traverse or clear.
#[pyclass(extends=PyDict, module = "ddtrace.internal.native._native")]
pub struct EventResultDict;

#[pymethods]
impl EventResultDict {
    #[new]
    fn py_new() -> Self {
        EventResultDict
    }

    fn __missing__(&self, key: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let py = key.py();
        Ok(get_missing_event(py)?.clone_ref(py))
    }

    fn __getattr__(slf: Bound<'_, Self>, name: &str) -> PyResult<Py<PyAny>> {
        let py = slf.py();
        match slf.as_any().cast::<PyDict>()?.get_item(name)? {
            Some(v) => Ok(v.unbind()),
            // Mirror Python __getattr__: dict lookup missing → return _MissingEvent
            None => Ok(get_missing_event(py)?.clone_ref(py)),
        }
    }
}

// Coerce any Python object to a tuple for use as call args.
// Accepts tuple (fast path, zero copy), list, or any iterable.
// On failure returns an empty tuple rather than propagating an error,
// so a misbehaving caller cannot crash the host application.
fn coerce_to_tuple<'py>(py: Python<'py>, args: Option<Py<PyAny>>) -> Bound<'py, PyTuple> {
    let Some(obj) = args else {
        return PyTuple::empty(py);
    };
    let bound = obj.into_bound(py);
    if let Ok(t) = bound.clone().cast_into::<PyTuple>() {
        return t;
    }
    if let Ok(l) = bound.clone().cast_into::<PyList>() {
        if let Ok(t) = PyTuple::new(py, l.iter()) {
            return t;
        }
    }
    // General iterable fallback
    let result = bound
        .try_iter()
        .and_then(|iter| iter.collect::<PyResult<Vec<_>>>())
        .and_then(|v| PyTuple::new(py, v));
    result.unwrap_or_else(|_| PyTuple::empty(py))
}

#[pyfunction]
pub fn has_listeners(event_id: &str) -> bool {
    LISTENERS
        .read()
        .unwrap()
        .get(event_id)
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

#[pyfunction]
#[pyo3(signature = (event_id, callback, name=None))]
pub fn on(
    py: Python<'_>,
    event_id: String,
    callback: Py<PyAny>,
    name: Option<Py<PyAny>>,
) -> PyResult<()> {
    let key: Py<PyAny> = match name {
        Some(n) => n,
        None => {
            let ptr = callback.as_ptr() as isize;
            ptr.into_pyobject(py)?.into_any().unbind()
        }
    };
    let mut guard = LISTENERS.write().unwrap();
    let vec = guard.entry(event_id).or_default();
    for (existing_key, existing_cb) in vec.iter_mut() {
        if existing_key.bind(py).eq(key.bind(py))? {
            *existing_cb = callback;
            return Ok(());
        }
    }
    vec.push((key, callback));
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (event_id=None, callback=None))]
pub fn reset(py: Python<'_>, event_id: Option<&str>, callback: Option<Py<PyAny>>) {
    let mut guard = LISTENERS.write().unwrap();
    if let Some(cb) = callback {
        if let Some(eid) = event_id {
            if let Some(vec) = guard.get_mut(eid) {
                // Use Python value equality so bound method objects compare correctly.
                // Python bound methods are always new objects (`a.m is not a.m`), so
                // pointer identity would never match. `__eq__` checks __func__ + __self__.
                vec.retain(|(_, stored_cb)| stored_cb.bind(py).ne(cb.bind(py)).unwrap_or(true));
            }
        }
    } else if let Some(eid) = event_id {
        guard.remove(eid);
    } else {
        guard.clear();
    }
}

#[inline(always)]
fn should_propagate(e: &PyErr, py: Python<'_>, allow_raise: bool) -> bool {
    // Mirrors `except Exception:` semantics: BaseException subclasses propagate always.
    !e.is_instance_of::<pyo3::exceptions::PyException>(py)
        || allow_raise
        || crate::config::get_raise()
}

#[pyfunction]
#[pyo3(signature = (event_id, args=None, allow_raise=false))]
pub fn dispatch(
    py: Python<'_>,
    event_id: &str,
    args: Option<Py<PyAny>>,
    allow_raise: bool,
) -> PyResult<()> {
    let callbacks = {
        let guard = LISTENERS.read().unwrap();
        let v = match guard.get(event_id) {
            None => return Ok(()),
            Some(v) if v.is_empty() => return Ok(()),
            Some(v) => v,
        };
        v.iter().map(|(_, cb)| cb.clone_ref(py)).collect::<Vec<_>>()
    };

    let call_args = coerce_to_tuple(py, args);

    for cb in &callbacks {
        if let Err(e) = cb.bind(py).call1(&call_args) {
            if should_propagate(&e, py, allow_raise) {
                return Err(e);
            }
        }
    }
    Ok(())
}

/// Returns the _MissingEventDict singleton when no listeners, otherwise an EventResultDict
/// mapping name_key -> EventResult. Returned directly — no Python wrapper needed.
#[pyfunction]
#[pyo3(signature = (event_id, args=None))]
pub fn dispatch_with_results(
    py: Python<'_>,
    event_id: &str,
    args: Option<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    let entries = {
        let guard = LISTENERS.read().unwrap();
        let v = match guard.get(event_id) {
            Some(v) if !v.is_empty() => v,
            _ => return Ok(get_missing_event_dict(py)?.clone_ref(py)),
        };
        v.iter()
            .map(|(k, cb)| (k.clone_ref(py), cb.clone_ref(py)))
            .collect::<Vec<_>>()
    };

    let call_args = coerce_to_tuple(py, args);

    let result_dict_py = Py::new(py, EventResultDict)?;
    {
        let result_dict_bound = result_dict_py.bind(py);
        let dict = result_dict_bound.as_any().cast::<PyDict>()?;
        for (key, cb) in entries {
            match cb.bind(py).call1(&call_args) {
                Ok(value) => {
                    let event_result = Py::new(
                        py,
                        EventResult {
                            response_type: Some(get_rt_ok(py)?.clone_ref(py)),
                            value: Some(value.unbind()),
                            exception: None,
                            is_ok: true,
                        },
                    )?;
                    dict.set_item(key.bind(py), event_result.bind(py))?;
                }
                Err(e) => {
                    if should_propagate(&e, py, false) {
                        return Err(e);
                    }
                    let exc = e.into_value(py).into_any();
                    let event_result = Py::new(
                        py,
                        EventResult {
                            response_type: Some(get_rt_exception(py)?.clone_ref(py)),
                            value: None,
                            exception: Some(exc),
                            is_ok: false,
                        },
                    )?;
                    dict.set_item(key.bind(py), event_result.bind(py))?;
                }
            }
        }
    }

    Ok(result_dict_py.into_any())
}

pub fn register_event_hub(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ResultType>()?;
    m.add_class::<EventResult>()?;
    m.add_class::<EventResultDict>()?;
    m.add_function(wrap_pyfunction!(has_listeners, m)?)?;
    m.add_function(wrap_pyfunction!(on, m)?)?;
    m.add_function(wrap_pyfunction!(reset, m)?)?;
    m.add_function(wrap_pyfunction!(dispatch, m)?)?;
    m.add_function(wrap_pyfunction!(dispatch_with_results, m)?)?;
    Ok(())
}
