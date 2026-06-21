//! WAF lifecycle bindings built on the high-level `libddwaf` crate (Builder/Handle/Context/
//! Subcontext). These types own their libddwaf resources and manage `Drop`/`Send`/`Sync`
//! themselves, so there are no raw pointers or manual frees here.
//!
//! Long-running eval releases the GIL via `Python::detach`. The high-level object types
//! (`WafMap`/`RunResult`) are not `Send` (they hold raw `ddwaf_object` pointers), so we move them
//! across the detached closure inside [`Ungil`] — sound because the closure has exclusive ownership
//! during the GIL release and `ddwaf_object` data is `Send` at the libddwaf-sys level.

use std::time::Duration;

use libddwaf::object::{WafMap, WafObject, WafOwnedDefaultAllocator};
use libddwaf::{
    Builder, Config, Context, Handle, Obfuscator, RunError, RunResult, RunnableContext, Subcontext,
};
use pyo3::prelude::*;

use crate::ddwaf::object::{build_object, read_map, read_object, Limits, Truncation};

/// No-limit build settings used for rulesets/configs (which are not truncated). `container_cap`
/// still applies the uint16 ceiling; `max_string_length` of `usize::MAX` disables truncation.
const NO_LIMITS: Limits = Limits {
    max_objects: usize::MAX,
    max_depth: 1000,
    max_string_length: usize::MAX,
};

/// Wraps a value so it can cross `Python::detach` even when it is not `Send`. See module note.
struct Ungil<T>(T);
// SAFETY: only used to move exclusively-owned libddwaf objects into/out of a detached closure; the
// underlying `ddwaf_object` data is `Send` (see libddwaf-sys), and there is no aliasing.
unsafe impl<T> Send for Ungil<T> {}

#[pyclass(name = "Builder", module = "ddtrace.internal.native._native.ddwaf")]
pub struct PyBuilder {
    inner: Builder,
}

#[pymethods]
impl PyBuilder {
    /// Creates a builder, applying the optional obfuscation key/value regexes as its base config.
    #[new]
    #[pyo3(signature = (key_regex=None, value_regex=None))]
    fn new(key_regex: Option<Vec<u8>>, value_regex: Option<Vec<u8>>) -> PyResult<Self> {
        let config = Config::new(Obfuscator::new(key_regex, value_regex));
        Builder::new(Some(&config))
            .map(|inner| PyBuilder { inner })
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("failed to initialize libddwaf builder")
            })
    }

    /// Adds or updates a config at `path`, returning `(ok, diagnostics)` where `diagnostics` is a
    /// Python dict.
    fn add_or_update_config<'py>(
        &mut self,
        py: Python<'py>,
        path: &str,
        ruleset: &Bound<'py, PyAny>,
    ) -> PyResult<(bool, Bound<'py, PyAny>)> {
        let mut trunc = Truncation::default();
        let obj = build_object(ruleset, NO_LIMITS, &mut trunc)?;
        let mut diagnostics = WafOwnedDefaultAllocator::<WafMap>::default();
        let ok = self
            .inner
            .add_or_update_config(path, &obj, Some(&mut diagnostics));
        let diag = read_map(py, Some(&diagnostics))?;
        Ok((ok, diag))
    }

    fn remove_config(&mut self, path: &str) -> bool {
        self.inner.remove_config(path)
    }

    fn config_paths_count(&mut self, filter: &str) -> u32 {
        self.inner.config_paths_count(Some(filter))
    }

    fn build_instance(&mut self) -> Option<PyHandle> {
        self.inner.build().map(|inner| PyHandle { inner })
    }
}

#[pyclass(name = "Handle", module = "ddtrace.internal.native._native.ddwaf")]
pub struct PyHandle {
    inner: Handle,
}

#[pymethods]
impl PyHandle {
    fn known_addresses(&self) -> Vec<String> {
        self.inner
            .known_addresses()
            .iter()
            .map(|c| c.to_string_lossy().into_owned())
            .collect()
    }

    fn new_context(&self) -> PyContext {
        PyContext {
            inner: self.inner.new_context(),
        }
    }
}

#[pyclass(name = "Context", module = "ddtrace.internal.native._native.ddwaf")]
pub struct PyContext {
    inner: Context,
}

#[pymethods]
impl PyContext {
    #[pyo3(signature = (data, max_objects, max_depth, max_string_length, timeout_us))]
    fn run<'py>(
        &mut self,
        py: Python<'py>,
        data: &Bound<'py, PyAny>,
        max_objects: usize,
        max_depth: i64,
        max_string_length: usize,
        timeout_us: u64,
    ) -> PyResult<WafResult> {
        run_eval(
            py,
            &mut self.inner,
            data,
            max_objects,
            max_depth,
            max_string_length,
            timeout_us,
        )
    }

    fn new_subcontext(&self) -> Option<PySubcontext> {
        self.inner
            .new_subcontext()
            .ok()
            .map(|inner| PySubcontext { inner })
    }
}

#[pyclass(name = "Subcontext", module = "ddtrace.internal.native._native.ddwaf")]
pub struct PySubcontext {
    inner: Subcontext,
}

#[pymethods]
impl PySubcontext {
    #[pyo3(signature = (data, max_objects, max_depth, max_string_length, timeout_us))]
    fn run<'py>(
        &mut self,
        py: Python<'py>,
        data: &Bound<'py, PyAny>,
        max_objects: usize,
        max_depth: i64,
        max_string_length: usize,
        timeout_us: u64,
    ) -> PyResult<WafResult> {
        run_eval(
            py,
            &mut self.inner,
            data,
            max_objects,
            max_depth,
            max_string_length,
            timeout_us,
        )
    }
}

/// The structured outcome of a WAF run, consumed by the Python `DDWaf.run`.
#[pyclass(name = "WafResult", module = "ddtrace.internal.native._native.ddwaf")]
pub struct WafResult {
    #[pyo3(get)]
    return_code: i32,
    #[pyo3(get)]
    events: Py<PyAny>,
    #[pyo3(get)]
    actions: Py<PyAny>,
    #[pyo3(get)]
    attributes: Py<PyAny>,
    #[pyo3(get)]
    keep: bool,
    #[pyo3(get)]
    timeout: bool,
    /// WAF-reported duration in microseconds.
    #[pyo3(get)]
    duration: f64,
    /// `(string_length, container_size, container_depth)` truncation markers from building the input.
    #[pyo3(get)]
    truncation: Py<PyAny>,
}

/// Builds the input map, evaluates it against `ctx` with the GIL released, and converts the result.
fn run_eval<'py, C: RunnableContext + Send>(
    py: Python<'py>,
    ctx: &mut C,
    data: &Bound<'py, PyAny>,
    max_objects: usize,
    max_depth: i64,
    max_string_length: usize,
    timeout_us: u64,
) -> PyResult<WafResult> {
    let limits = Limits {
        max_objects,
        max_depth,
        max_string_length,
    };
    let mut trunc = Truncation::default();
    let obj = build_object(data, limits, &mut trunc)?;
    let map = WafMap::try_from(obj).unwrap_or_else(|_| WafMap::new(0));
    let timeout = Duration::from_micros(timeout_us);

    // Move the (non-Send) input map into the detached closure and the (non-Send) result back out.
    let input = Ungil(map);
    let outcome = py.detach(move || Ungil(ctx.run(input.0, timeout))).0;

    let truncation = trunc.into_py(py)?;
    build_result(py, outcome, truncation)
}

/// Converts a libddwaf run outcome into a [`WafResult`].
fn build_result(
    py: Python<'_>,
    outcome: Result<RunResult, RunError>,
    truncation: Bound<'_, PyAny>,
) -> PyResult<WafResult> {
    let empty_list = pyo3::types::PyList::empty(py).into_any();
    let empty_dict = pyo3::types::PyDict::new(py).into_any();

    let (return_code, output) = match outcome {
        Ok(RunResult::NoMatch(o)) => (0, Some(o)),
        Ok(RunResult::Match(o)) => (1, Some(o)),
        Err(RunError::InvalidArgument) => (-1, None),
        Err(RunError::InvalidObject) => (-2, None),
        // InternalError and any future (non_exhaustive) RunError variant.
        Err(_) => (-3, None),
    };

    let (events, actions, attributes, keep, timeout, duration) = if let Some(o) = output {
        let events = match o.events() {
            Some(k) => read_object(py, k.value().as_object())?,
            None => empty_list.clone(),
        };
        let actions = match o.actions() {
            Some(k) => read_object(py, k.value().as_object())?,
            None => empty_dict.clone(),
        };
        let attributes = match o.attributes() {
            Some(k) => read_object(py, k.value().as_object())?,
            None => empty_dict.clone(),
        };
        #[allow(clippy::cast_precision_loss)]
        let duration_us = o.duration().as_nanos() as f64 / 1000.0;
        (
            events,
            actions,
            attributes,
            o.keep(),
            o.timeout(),
            duration_us,
        )
    } else {
        (
            empty_list,
            empty_dict.clone(),
            empty_dict,
            false,
            false,
            0.0,
        )
    };

    Ok(WafResult {
        return_code,
        events: events.unbind(),
        actions: actions.unbind(),
        attributes: attributes.unbind(),
        keep,
        timeout,
        duration,
        truncation: truncation.unbind(),
    })
}

/// Returns the underlying libddwaf version string.
#[pyfunction]
pub fn version() -> String {
    libddwaf::version().to_string_lossy().into_owned()
}

/// Builds a `WafObject` from a Python value then reads it back — a round-trip used by the object
/// marshalling tests. Returns `(value, (string_length, container_size, container_depth))`.
#[pyfunction]
#[pyo3(signature = (value, max_objects, max_depth, max_string_length))]
pub fn ddwaf_object_roundtrip<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    max_objects: usize,
    max_depth: i64,
    max_string_length: usize,
) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    let limits = Limits {
        max_objects,
        max_depth,
        max_string_length,
    };
    let mut trunc = Truncation::default();
    let obj: WafObject = build_object(value, limits, &mut trunc)?;
    Ok((read_object(py, &obj)?, trunc.into_py(py)?))
}

/// Registers the WAF lifecycle types and functions on the `ddwaf` submodule.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyBuilder>()?;
    m.add_class::<PyHandle>()?;
    m.add_class::<PyContext>()?;
    m.add_class::<PySubcontext>()?;
    m.add_class::<WafResult>()?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_roundtrip, m)?)?;
    Ok(())
}
