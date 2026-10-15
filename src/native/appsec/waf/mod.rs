//! AppSec WAF bindings registered in the tracer native extension.
pub mod convert;

use convert::{convert, materialize, Limits, Stats};
use libddwaf::object::{WafMap, WafObject, WafOwnedDefaultAllocator, WafString};
use libddwaf::{RunError, RunOutput, RunResult, RunnableContext};
pyo3::create_exception!(ddtrace_native, EvaluationError, PyRuntimeError);
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::gc::{PyTraverseError, PyVisit};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyList, PyString};
use std::mem::ManuallyDrop;
use std::ops::Deref;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

// Never touch or destroy inherited native synchronization/state in a fork child.
// A fresh builder and contexts are required in that process.
struct ProcessOwned<T> {
    pid: u32,
    value: ManuallyDrop<T>,
}
impl<T> ProcessOwned<T> {
    fn new(value: T) -> Self {
        Self {
            pid: std::process::id(),
            value: ManuallyDrop::new(value),
        }
    }
    fn check(&self) -> PyResult<()> {
        if self.pid != std::process::id() {
            return Err(PyRuntimeError::new_err(
                "WAF objects cannot be reused after fork; create a fresh WAF in the child",
            ));
        }
        Ok(())
    }
}
impl<T> Deref for ProcessOwned<T> {
    type Target = T;
    fn deref(&self) -> &T {
        &self.value
    }
}
impl<T> Drop for ProcessOwned<T> {
    fn drop(&mut self) {
        if self.pid == std::process::id() {
            // SAFETY: the value belongs to this process and is dropped exactly once.
            unsafe {
                ManuallyDrop::drop(&mut self.value);
            }
        }
    }
}

fn stats_snapshot(
    py: Python<'_>,
    stats: &Stats,
    cache: &OnceLock<Py<Stats>>,
) -> PyResult<Py<Stats>> {
    if let Some(value) = cache.get() {
        return Ok(value.clone_ref(py));
    }
    let value = Py::new(py, stats.clone())?;
    let _ = cache.set(value.clone_ref(py));
    Ok(value)
}

fn limits(objects: usize, depth: usize, string: usize, compatibility: bool) -> PyResult<Limits> {
    if depth > 256 {
        return Err(PyValueError::new_err(
            "max_depth must be <= 256 to bound native recursion",
        ));
    }
    Ok(Limits {
        objects,
        depth,
        string,
        compatibility,
    })
}

fn error(error: impl std::fmt::Display) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

#[pyclass(module = "ddtrace.internal.native._native.ddwaf", frozen)]
pub struct Encoded {
    object: WafObject,
    stats: Stats,
    stats_py: OnceLock<Py<Stats>>,
}

#[pymethods]
impl Encoded {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Some(stats) = self.stats_py.get() {
            visit.call(stats)?;
        }
        Ok(())
    }

    fn bytes(&self, py: Python<'_>) -> PyResult<Py<PyBytes>> {
        let string = self
            .object
            .as_type::<WafString>()
            .ok_or_else(|| PyValueError::new_err("encoded value is not a string"))?;
        Ok(PyBytes::new(py, string.as_bytes()).unbind())
    }

    fn materialize(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        materialize(py, &self.object)
    }

    #[getter]
    fn stats(&self, py: Python<'_>) -> PyResult<Py<Stats>> {
        stats_snapshot(py, &self.stats, &self.stats_py)
    }
}

#[pyfunction]
#[pyo3(signature = (data, max_objects=256, max_depth=20, max_string_length=4096, compatibility=false))]
fn encode(
    data: &Bound<'_, PyAny>,
    max_objects: usize,
    max_depth: usize,
    max_string_length: usize,
    compatibility: bool,
) -> PyResult<Encoded> {
    let mut stats = Stats::default();
    let object = convert(
        data,
        limits(max_objects, max_depth, max_string_length, compatibility)?,
        &mut stats,
    )?;
    Ok(Encoded {
        object,
        stats,
        stats_py: OnceLock::new(),
    })
}

#[pyfunction]
fn version() -> &'static str {
    libddwaf::version().to_str().unwrap_or("invalid version")
}

#[pyclass(module = "ddtrace.internal.native._native.ddwaf", frozen, subclass)]
struct Builder {
    inner: ProcessOwned<Mutex<libddwaf::Builder>>,
}

#[pymethods]
impl Builder {
    #[new]
    fn new() -> PyResult<Self> {
        Ok(Self {
            inner: ProcessOwned::new(Mutex::new(
                libddwaf::Builder::new(None).ok_or_else(|| error("builder init failed"))?,
            )),
        })
    }

    fn add_config(
        &self,
        py: Python<'_>,
        path: &str,
        data: &Bound<'_, PyAny>,
    ) -> PyResult<(bool, Py<PyAny>)> {
        self.inner.check()?;
        if path.is_empty() || path.len() > u32::MAX as usize {
            return Err(PyValueError::new_err("invalid configuration path"));
        }
        let rules = if let Ok(json) = data.cast_exact::<PyBytes>() {
            WafObject::from_json(json.as_bytes())
                .ok_or_else(|| PyValueError::new_err("invalid rules JSON"))?
        } else {
            let object = convert(
                data,
                Limits {
                    objects: 65535,
                    depth: 256,
                    string: u32::MAX as usize,
                    compatibility: true,
                },
                &mut Stats::default(),
            )?;
            // Both JSON and Python inputs must use the same ownership wrapper below.
            return self.add_object(py, path, &object);
        };
        self.add_object(py, path, &rules)
    }

    #[pyo3(signature = (filter_str=None))]
    fn config_paths_count(&self, py: Python<'_>, filter_str: Option<&str>) -> PyResult<u32> {
        self.inner.check()?;
        if filter_str.is_some_and(|value| value.len() > u32::MAX as usize) {
            return Err(PyValueError::new_err("invalid configuration filter_str"));
        }
        py.detach(|| {
            Ok(self
                .inner
                .lock()
                .map_err(error)?
                .config_paths_count(filter_str))
        })
    }

    fn remove_config(&self, py: Python<'_>, path: &str) -> PyResult<bool> {
        self.inner.check()?;
        if path.len() > u32::MAX as usize {
            return Err(PyValueError::new_err("invalid configuration path"));
        }
        py.detach(|| Ok(self.inner.lock().map_err(error)?.remove_config(path)))
    }

    fn build(&self, py: Python<'_>) -> PyResult<Option<Engine>> {
        self.inner.check()?;
        py.detach(|| {
            Ok(self
                .inner
                .lock()
                .map_err(error)?
                .build()
                .map(|handle| Engine {
                    inner: Arc::new(ProcessOwned::new(handle)),
                }))
        })
    }
}

impl Builder {
    fn add_object(
        &self,
        py: Python<'_>,
        path: &str,
        object: &WafObject,
    ) -> PyResult<(bool, Py<PyAny>)> {
        let (success, diagnostics) = py.detach(|| {
            let mut diagnostics = WafOwnedDefaultAllocator::<WafMap>::default();
            let success = self.inner.lock().map_err(error)?.add_or_update_config(
                path,
                object,
                Some(&mut diagnostics),
            );
            Ok::<_, PyErr>((success, diagnostics))
        })?;
        Ok((success, materialize(py, diagnostics.as_object())?))
    }
}

#[pyclass(module = "ddtrace.internal.native._native.ddwaf", frozen)]
struct Engine {
    inner: Arc<ProcessOwned<libddwaf::Handle>>,
}

#[pymethods]
impl Engine {
    #[staticmethod]
    fn from_json(py: Python<'_>, json: &Bound<'_, PyBytes>) -> PyResult<Self> {
        let builder = Builder::new()?;
        builder.add_config(py, "rules", json)?;
        builder.build(py)?.ok_or_else(|| error("no active rules"))
    }

    #[getter]
    fn required_data(&self) -> PyResult<Vec<String>> {
        self.inner.check()?;
        Ok(self
            .inner
            .known_addresses()
            .into_iter()
            .map(|value| value.to_string_lossy().into_owned())
            .collect())
    }

    fn context(&self, py: Python<'_>) -> PyResult<Context> {
        self.inner.check()?;
        let owner = Arc::clone(&self.inner);
        let inner = py.detach(|| owner.new_context());
        Ok(Context {
            inner: ProcessOwned::new(Mutex::new(NativeContext::Root(inner))),
            _engine: owner,
        })
    }
}

enum NativeContext {
    Root(libddwaf::Context),
    Child(libddwaf::Subcontext),
}

#[pyclass(module = "ddtrace.internal.native._native.ddwaf", frozen)]
struct Context {
    inner: ProcessOwned<Mutex<NativeContext>>,
    // Explicit lifetime invariant even if native libddwaf retains the ruleset internally.
    _engine: Arc<ProcessOwned<libddwaf::Handle>>,
}

#[pymethods]
impl Context {
    #[pyo3(signature = (data, timeout_us=5000, max_objects=256, max_depth=20, max_string_length=4096, compatibility=false))]
    #[allow(clippy::too_many_arguments)] // Explicit conversion limits on the Python API.
    fn run(
        &self,
        py: Python<'_>,
        data: &Bound<'_, PyAny>,
        timeout_us: u64,
        max_objects: usize,
        max_depth: usize,
        max_string_length: usize,
        compatibility: bool,
    ) -> PyResult<WafResult> {
        self.inner.check()?;
        let started = Instant::now();
        let mut stats = Stats::default();
        let object = convert(
            data,
            limits(max_objects, max_depth, max_string_length, compatibility)?,
            &mut stats,
        )?;
        let object = WafMap::try_from(object)
            .map_err(|_| PyValueError::new_err("run input must be a mapping"))?;
        // No Python references cross the detached section. Lock acquisition is detached too.
        let result = py.detach(|| {
            let mut context = self.inner.lock().map_err(error)?;
            match &mut *context {
                NativeContext::Root(context) => {
                    context.run(object, Duration::from_micros(timeout_us))
                }
                NativeContext::Child(context) => {
                    context.run(object, Duration::from_micros(timeout_us))
                }
            }
            .map_err(|cause| {
                let code = match cause {
                    RunError::InternalError => -3,
                    RunError::InvalidObject => -2,
                    RunError::InvalidArgument => -1,
                    _ => -3,
                };
                EvaluationError::new_err((code, cause.to_string()))
            })
        })?;
        let (matched, output) = match result {
            RunResult::Match(output) => (true, output),
            RunResult::NoMatch(output) => (false, output),
        };
        Ok(WafResult {
            matched,
            error_code: None,
            output: Some(output),
            stats,
            stats_py: OnceLock::new(),
            events: OnceLock::new(),
            actions: OnceLock::new(),
            attributes: OnceLock::new(),
            groups: OnceLock::new(),
            started: Some(started),
            total_duration_ns: started.elapsed().as_nanos() as u64,
            duration: 0,
            timed_out: false,
            should_keep: false,
        })
    }

    fn subcontext(&self, py: Python<'_>) -> PyResult<Context> {
        self.inner.check()?;
        let inner = py.detach(|| {
            let context = self.inner.lock().map_err(error)?;
            match &*context {
                NativeContext::Root(context) => context.new_subcontext().map_err(error),
                NativeContext::Child(_) => {
                    Err(PyValueError::new_err("nested subcontexts are unsupported"))
                }
            }
        })?;
        Ok(Context {
            inner: ProcessOwned::new(Mutex::new(NativeContext::Child(inner))),
            _engine: Arc::clone(&self._engine),
        })
    }
}

#[pyclass(module = "ddtrace.internal.native._native.ddwaf", get_all)]
struct RulesetInfo {
    version: String,
    accepted_rules: usize,
    rejected_rules: usize,
    errors: Option<Py<PyDict>>,
}

#[pymethods]
impl RulesetInfo {
    #[new]
    #[pyo3(signature = (version, accepted_rules, rejected_rules, errors=None))]
    fn new(
        py: Python<'_>,
        version: String,
        accepted_rules: usize,
        rejected_rules: usize,
        errors: Option<Py<PyDict>>,
    ) -> Self {
        Self {
            version,
            accepted_rules,
            rejected_rules,
            errors: Some(errors.unwrap_or_else(|| PyDict::new(py).unbind())),
        }
    }
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Some(errors) = &self.errors {
            visit.call(errors)?;
        }
        Ok(())
    }
    fn __clear__(&mut self) {
        self.errors.take();
    }
}

struct AttributeGroups {
    meta_tags: Py<PyDict>,
    metrics: Py<PyDict>,
    api_security: Py<PyDict>,
}

#[pyclass(name = "Result", module = "ddtrace.internal.native._native.ddwaf")]
struct WafResult {
    #[pyo3(get)]
    matched: bool,
    #[pyo3(get)]
    error_code: Option<i32>,
    output: Option<RunOutput>,
    stats: Stats,
    stats_py: OnceLock<Py<Stats>>,
    events: OnceLock<Py<PyAny>>,
    actions: OnceLock<Py<PyAny>>,
    attributes: OnceLock<Py<PyAny>>,
    groups: OnceLock<AttributeGroups>,
    started: Option<Instant>,
    #[pyo3(get)]
    total_duration_ns: u64,
    duration: u64,
    timed_out: bool,
    should_keep: bool,
}

fn cached(
    py: Python<'_>,
    cache: &OnceLock<Py<PyAny>>,
    build: impl FnOnce() -> PyResult<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    if let Some(value) = cache.get() {
        return Ok(value.clone_ref(py));
    }
    let value = build()?;
    let _ = cache.set(value.clone_ref(py));
    Ok(value)
}

impl WafResult {
    fn attribute_groups(&self, py: Python<'_>) -> PyResult<&AttributeGroups> {
        if self.groups.get().is_none() {
            let attributes = self.attributes(py)?;
            let attributes = attributes.bind(py).cast::<PyDict>()?;
            let meta_tags = PyDict::new(py);
            let metrics = PyDict::new(py);
            let api_security = PyDict::new(py);
            for (key, value) in attributes.iter() {
                let name = key.extract::<&str>()?;
                if name.starts_with("_dd.appsec.s.") {
                    api_security.set_item(key, value)?;
                } else if value.is_instance_of::<PyString>() {
                    meta_tags.set_item(key, value)?;
                } else if value.is_instance_of::<PyBool>() {
                    metrics.set_item(key, i32::from(value.extract::<bool>()?))?;
                } else {
                    metrics.set_item(key, value)?;
                }
            }
            let _ = self.groups.set(AttributeGroups {
                meta_tags: meta_tags.unbind(),
                metrics: metrics.unbind(),
                api_security: api_security.unbind(),
            });
        }
        Ok(self.groups.get().expect("initialized attribute groups"))
    }
}

#[pymethods]
impl WafResult {
    #[new]
    #[pyo3(signature = (*, matched=false, error_code=None, events=None, actions=None, duration_ns=0, total_duration_ns=0, timeout=false, stats=None, attributes=None, keep=false))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        matched: bool,
        error_code: Option<i32>,
        events: Option<Py<PyList>>,
        actions: Option<Py<PyDict>>,
        duration_ns: u64,
        total_duration_ns: u64,
        timeout: bool,
        stats: Option<PyRef<'_, Stats>>,
        attributes: Option<Py<PyDict>>,
        keep: bool,
    ) -> PyResult<Self> {
        if error_code.is_some_and(|code| code >= 0) {
            return Err(PyValueError::new_err("error_code must be negative"));
        }
        if matched && error_code.is_some() {
            return Err(PyValueError::new_err("an error result cannot also match"));
        }
        Ok(Self {
            matched,
            error_code,
            output: None,
            stats: stats.map_or_else(Stats::default, |value| (*value).clone()),
            stats_py: OnceLock::new(),
            events: OnceLock::from(
                events
                    .unwrap_or_else(|| PyList::empty(py).unbind())
                    .into_any(),
            ),
            actions: OnceLock::from(
                actions
                    .unwrap_or_else(|| PyDict::new(py).unbind())
                    .into_any(),
            ),
            attributes: OnceLock::from(
                attributes
                    .unwrap_or_else(|| PyDict::new(py).unbind())
                    .into_any(),
            ),
            groups: OnceLock::new(),
            started: None,
            total_duration_ns,
            duration: duration_ns,
            timed_out: timeout,
            should_keep: keep,
        })
    }

    // Exposed containers can reference the result; all cached references participate in GC.
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Some(value) = self.stats_py.get() {
            visit.call(value)?;
        }
        for cache in [&self.events, &self.actions, &self.attributes] {
            if let Some(value) = cache.get() {
                visit.call(value)?;
            }
        }
        if let Some(groups) = self.groups.get() {
            visit.call(&groups.meta_tags)?;
            visit.call(&groups.metrics)?;
            visit.call(&groups.api_security)?;
        }
        Ok(())
    }
    fn __clear__(&mut self) {
        self.stats_py.take();
        self.events.take();
        self.actions.take();
        self.attributes.take();
        self.groups.take();
    }

    /// Materialize the request outputs once and freeze total conversion/evaluation time.
    fn prepare(mut slf: PyRefMut<'_, Self>) -> PyResult<PyRefMut<'_, Self>> {
        let py = slf.py();
        slf.events(py)?;
        slf.actions(py)?;
        slf.attribute_groups(py)?;
        if let Some(started) = slf.started.take() {
            slf.total_duration_ns = started.elapsed().as_nanos().min(u64::MAX as u128) as u64;
        }
        Ok(slf)
    }
    #[getter]
    fn timeout(&self) -> bool {
        self.output
            .as_ref()
            .map_or(self.timed_out, RunOutput::timeout)
    }
    #[getter]
    fn keep(&self) -> bool {
        self.output
            .as_ref()
            .map_or(self.should_keep, RunOutput::keep)
    }
    #[getter]
    fn duration_ns(&self) -> u64 {
        self.output
            .as_ref()
            .map_or(self.duration, |value| value.duration().as_nanos() as u64)
    }
    #[getter]
    fn evaluated(&self) -> u64 {
        self.output.as_ref().map_or(0, RunOutput::evaluated)
    }
    #[getter]
    fn stats(&self, py: Python<'_>) -> PyResult<Py<Stats>> {
        stats_snapshot(py, &self.stats, &self.stats_py)
    }
    #[getter]
    fn events(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        cached(py, &self.events, || {
            match self.output.as_ref().and_then(RunOutput::events) {
                Some(value) => materialize(py, value.value().as_object()),
                None => Ok(PyList::empty(py).into_any().unbind()),
            }
        })
    }
    #[getter]
    fn actions(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        cached(py, &self.actions, || {
            match self.output.as_ref().and_then(RunOutput::actions) {
                Some(value) => materialize(py, value.value().as_object()),
                None => Ok(PyDict::new(py).into_any().unbind()),
            }
        })
    }
    #[getter]
    fn attributes(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        cached(py, &self.attributes, || {
            match self.output.as_ref().and_then(RunOutput::attributes) {
                Some(value) => materialize(py, value.value().as_object()),
                None => Ok(PyDict::new(py).into_any().unbind()),
            }
        })
    }
    #[getter]
    fn meta_tags(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        Ok(self.attribute_groups(py)?.meta_tags.clone_ref(py))
    }
    #[getter]
    fn metrics(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        Ok(self.attribute_groups(py)?.metrics.clone_ref(py))
    }
    #[getter]
    fn api_security(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        Ok(self.attribute_groups(py)?.api_security.clone_ref(py))
    }
}

/// Register in an existing module; the consumer builds the only extension module.
pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.gil_used(true)?;
    module.add_function(wrap_pyfunction!(encode, module)?)?;
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_class::<Encoded>()?;
    module.add_class::<Stats>()?;
    module.add("EvaluationError", module.py().get_type::<EvaluationError>())?;
    module.add_class::<Builder>()?;
    module.add_class::<Engine>()?;
    module.add_class::<Context>()?;
    module.add_class::<WafResult>()?;
    module.add_class::<RulesetInfo>()?;
    // The embedding consumer chooses the module name; no tracer imports are needed.
    let name = module.name()?;
    for class in [
        "Encoded",
        "Stats",
        "EvaluationError",
        "Builder",
        "Engine",
        "Context",
        "Result",
        "RulesetInfo",
    ] {
        module.getattr(class)?.setattr("__module__", &name)?;
    }
    Ok(())
}
