use pyo3::{
    prelude::*,
    types::{PyDict, PyTuple},
};
use std::collections::HashMap;
use std::sync::{LazyLock, OnceLock, RwLock};

type Listeners = HashMap<String, Vec<(Py<PyAny>, Py<PyAny>)>>;

// (name_key, callback) pairs per event_id
static LISTENERS: LazyLock<RwLock<Listeners>> = LazyLock::new(|| RwLock::new(HashMap::new()));

// Cached Python objects — initialized on first use, never invalidated
static DDTRACE_CONFIG: OnceLock<Py<PyAny>> = OnceLock::new();
static RESULT_TYPE_OK: OnceLock<Py<PyAny>> = OnceLock::new();
static RESULT_TYPE_EXCEPTION: OnceLock<Py<PyAny>> = OnceLock::new();
static MISSING_EVENT: OnceLock<Py<PyAny>> = OnceLock::new();
static MISSING_EVENT_DICT: OnceLock<Py<PyAny>> = OnceLock::new();

// OnceLock::get_or_try_init is unstable; use get+set pattern instead.
// If two threads race, one wins and the other silently drops its value — both
// end up reading the same cached object via the final get().unwrap().
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

fn get_ddtrace_config(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(DDTRACE_CONFIG, py, {
        let module = py.import("ddtrace.internal.settings._config")?;
        Ok(module.getattr("config")?.unbind())
    })
}

fn should_raise(py: Python<'_>) -> bool {
    get_ddtrace_config(py)
        .and_then(|c| c.getattr(py, "_raise"))
        .and_then(|v| v.extract::<bool>(py))
        .unwrap_or(false)
}

fn get_result_type_ok(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(RESULT_TYPE_OK, py, {
        // Lazy import: safe because this is called long after both modules are loaded
        let module = py.import("ddtrace.internal.core.event_hub")?;
        Ok(module.getattr("ResultType")?.getattr("RESULT_OK")?.unbind())
    })
}

fn get_result_type_exception(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(RESULT_TYPE_EXCEPTION, py, {
        let module = py.import("ddtrace.internal.core.event_hub")?;
        Ok(module
            .getattr("ResultType")?
            .getattr("RESULT_EXCEPTION")?
            .unbind())
    })
}

fn get_missing_event(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(MISSING_EVENT, py, {
        // Lazy import: RESULT_UNDEFINED is in the Python shim, safe after full load
        let module = py.import("ddtrace.internal.core.event_hub")?;
        let result_type_undefined = module
            .getattr("ResultType")?
            .getattr("RESULT_UNDEFINED")?
            .unbind();
        let event_result = Py::new(
            py,
            EventResult {
                response_type: result_type_undefined,
                value: py_none(py),
                exception: py_none(py),
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

fn py_none(py: Python<'_>) -> Py<PyAny> {
    // py.None() returns Py<PyNone>; into_any() widens to Py<PyAny>
    py.None().into_any()
}

/// Native Python class mirroring the old Python EventResult dataclass.
/// Stores response_type, value, exception as Python objects.
/// is_ok is a private fast path for __bool__ that avoids Python equality.
#[pyclass(module = "ddtrace.internal.native._native")]
pub struct EventResult {
    #[pyo3(get, set)]
    pub response_type: Py<PyAny>,
    #[pyo3(get, set)]
    pub value: Py<PyAny>,
    #[pyo3(get, set)]
    pub exception: Py<PyAny>,
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
            .and_then(|rt| rt.getattr(py, "value").ok())
            .and_then(|v| v.extract::<i32>(py).ok())
            .map(|v| v == 0)
            .unwrap_or(false);
        Self {
            response_type: response_type.unwrap_or_else(|| py_none(py)),
            value: value.unwrap_or_else(|| py_none(py)),
            exception: exception.unwrap_or_else(|| py_none(py)),
            is_ok,
        }
    }

    fn __bool__(&self) -> bool {
        self.is_ok
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        Ok(format!(
            "EventResult(response_type={}, value={}, exception={})",
            self.response_type.bind(py).repr()?,
            self.value.bind(py).repr()?,
            self.exception.bind(py).repr()?,
        ))
    }
}

/// Native dict subclass mirroring the old Python EventResultDict.
/// __missing__ returns the _MissingEvent singleton for absent keys.
/// __getattr__ enables attribute-style access: result.listener_name.
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
pub fn reset(py: Python<'_>, event_id: Option<&str>, callback: Option<Py<PyAny>>) -> PyResult<()> {
    let mut guard = LISTENERS.write().unwrap();
    if let Some(cb) = callback {
        if let Some(eid) = event_id {
            if let Some(vec) = guard.get_mut(eid) {
                let mut err: Option<PyErr> = None;
                vec.retain(|(_, stored_cb)| {
                    if err.is_some() {
                        return true;
                    }
                    match stored_cb.bind(py).ne(cb.bind(py)) {
                        Ok(should_retain) => should_retain,
                        Err(e) => {
                            err = Some(e);
                            true
                        }
                    }
                });
                if let Some(e) = err {
                    return Err(e);
                }
            }
        }
    } else if let Some(eid) = event_id {
        guard.remove(eid);
    } else {
        guard.clear();
    }
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (event_id, args=None))]
pub fn dispatch(py: Python<'_>, event_id: &str, args: Option<Bound<'_, PyTuple>>) -> PyResult<()> {
    let callbacks = {
        let guard = LISTENERS.read().unwrap();
        let v = match guard.get(event_id) {
            None => return Ok(()),
            Some(v) if v.is_empty() => return Ok(()),
            Some(v) => v,
        };
        v.iter().map(|(_, cb)| cb.clone_ref(py)).collect::<Vec<_>>()
    };

    let empty;
    let call_args: &Bound<'_, PyTuple> = match args {
        Some(ref a) => a,
        None => {
            empty = PyTuple::empty(py);
            &empty
        }
    };

    for cb in &callbacks {
        if let Err(e) = cb.bind(py).call1(call_args) {
            if should_raise(py) {
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
    args: Option<Bound<'_, PyTuple>>,
) -> PyResult<Py<PyAny>> {
    let entries = {
        let guard = LISTENERS.read().unwrap();
        let v = match guard.get(event_id) {
            None => return Ok(get_missing_event_dict(py)?.clone_ref(py)),
            Some(v) if v.is_empty() => return Ok(get_missing_event_dict(py)?.clone_ref(py)),
            Some(v) => v,
        };
        v.iter()
            .map(|(k, cb)| (k.clone_ref(py), cb.clone_ref(py)))
            .collect::<Vec<_>>()
    };

    let empty;
    let call_args: &Bound<'_, PyTuple> = match args {
        Some(ref a) => a,
        None => {
            empty = PyTuple::empty(py);
            &empty
        }
    };

    let result_type_ok = get_result_type_ok(py)?;
    let result_type_exception = get_result_type_exception(py)?;

    let result_dict_py = Py::new(py, EventResultDict)?;
    {
        let result_dict_bound = result_dict_py.bind(py);
        let dict = result_dict_bound.as_any().cast::<PyDict>()?;
        for (key, cb) in entries {
            match cb.bind(py).call1(call_args) {
                Ok(value) => {
                    let event_result = Py::new(
                        py,
                        EventResult {
                            response_type: result_type_ok.clone_ref(py),
                            value: value.unbind(),
                            exception: py_none(py),
                            is_ok: true,
                        },
                    )?;
                    dict.set_item(key.bind(py), event_result.bind(py))?;
                }
                Err(e) => {
                    if should_raise(py) {
                        return Err(e);
                    }
                    let exc = e.into_value(py).into_any();
                    let event_result = Py::new(
                        py,
                        EventResult {
                            response_type: result_type_exception.clone_ref(py),
                            value: py_none(py),
                            exception: exc,
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
    m.add_class::<EventResult>()?;
    m.add_class::<EventResultDict>()?;
    m.add_function(wrap_pyfunction!(has_listeners, m)?)?;
    m.add_function(wrap_pyfunction!(on, m)?)?;
    m.add_function(wrap_pyfunction!(reset, m)?)?;
    m.add_function(wrap_pyfunction!(dispatch, m)?)?;
    m.add_function(wrap_pyfunction!(dispatch_with_results, m)?)?;
    Ok(())
}
