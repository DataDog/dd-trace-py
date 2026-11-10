//! Python WAF boundary backed by libddwaf-rust.
//!
//! Convert directly into owned WAF storage while attached, evaluate with the
//! interpreter detached, and decode borrowed results directly into Python.

mod convert;

use convert::{decode, describe, Encoder, Limits, Truncations};
use libddwaf::object::{WafMap, WafObject, WafOwnedDefaultAllocator};
use libddwaf::{RunError, RunResult, RunnableContext};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};
use std::ffi::CStr;
use std::mem::ManuallyDrop;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

fn checked_version() -> PyResult<&'static CStr> {
    let version = libddwaf::version();
    if version.is_empty() {
        return Err(PyRuntimeError::new_err("libddwaf could not be loaded"));
    }
    if version.to_bytes() != libddwaf::VERSION.as_bytes() {
        return Err(PyRuntimeError::new_err(format!(
            "expected libddwaf {}, found {}",
            libddwaf::VERSION,
            version.to_string_lossy()
        )));
    }
    Ok(version)
}

#[pyfunction]
fn version() -> PyResult<String> {
    Ok(checked_version()?.to_string_lossy().into_owned())
}

#[inline]
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Tracks operations in flight on a `Builder` across `fork()`. A quiescent builder
/// (`count == 0` at fork time) remains usable in the child so prefork workers keep the
/// pre-built ruleset and configuration state; a builder that was mid-operation in another
/// thread when `fork()` happened is poisoned in the child.
struct InFlight {
    /// Upper 32 bits: PID with active operations. Lower 32 bits: active operation count.
    state: AtomicU64,
}

impl InFlight {
    const fn new() -> Self {
        Self {
            state: AtomicU64::new(0),
        }
    }

    #[inline]
    fn enter(&self) -> PyResult<InFlightGuard<'_>> {
        let pid = std::process::id();
        let mut cur = self.state.load(Ordering::Acquire);
        loop {
            let cur_pid = (cur >> 32) as u32;
            let count = cur as u32;
            if count > 0 && cur_pid != pid {
                return Err(PyRuntimeError::new_err(
                    "WAF builder was in use during fork and cannot be used in the child",
                ));
            }
            let next = ((pid as u64) << 32) | ((count + 1) as u64);
            match self
                .state
                .compare_exchange_weak(cur, next, Ordering::AcqRel, Ordering::Acquire)
            {
                Ok(_) => return Ok(InFlightGuard(self)),
                Err(actual) => cur = actual,
            }
        }
    }

    #[inline]
    fn can_drop(&self) -> bool {
        let cur = self.state.load(Ordering::Acquire);
        (cur as u32) == 0 || ((cur >> 32) as u32) == std::process::id()
    }
}

struct InFlightGuard<'a>(&'a InFlight);

impl Drop for InFlightGuard<'_> {
    #[inline]
    fn drop(&mut self) {
        self.0.state.fetch_sub(1, Ordering::Release);
    }
}

/// Guards per-request native `Context` state and its mutex across `fork()`: an inherited
/// request context is neither evaluated nor destroyed in a child process.
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

    #[inline]
    fn get(&self) -> PyResult<&T> {
        if self.pid != std::process::id() {
            return Err(PyRuntimeError::new_err(
                "WAF context cannot be reused after fork; create a fresh context in the child",
            ));
        }
        Ok(&self.value)
    }
}

impl<T> Drop for ProcessOwned<T> {
    fn drop(&mut self) {
        if self.pid == std::process::id() {
            // SAFETY: owned by this process and dropped at most once.
            unsafe { ManuallyDrop::drop(&mut self.value) };
        }
    }
}

#[pyclass(frozen, module = "ddtrace.internal.native._native.ddwaf")]
struct Builder {
    raw: Mutex<ManuallyDrop<libddwaf::Builder>>,
    in_flight: InFlight,
}

impl Drop for Builder {
    fn drop(&mut self) {
        if self.in_flight.can_drop() {
            if let Ok(raw) = self.raw.get_mut() {
                // SAFETY: dropped once and never while inherited work is in flight.
                unsafe { ManuallyDrop::drop(raw) };
            }
        }
    }
}

fn validate_path(path: &str) -> PyResult<()> {
    u32::try_from(path.len())
        .map(|_| ())
        .map_err(|_| PyValueError::new_err("configuration path is too long"))
}

#[pymethods]
impl Builder {
    #[new]
    fn new() -> PyResult<Self> {
        checked_version()?;
        let raw = libddwaf::Builder::new(None)
            .ok_or_else(|| PyRuntimeError::new_err("ddwaf_builder_init failed"))?;
        Ok(Self {
            raw: Mutex::new(ManuallyDrop::new(raw)),
            in_flight: InFlight::new(),
        })
    }

    fn add_or_update_config<'py>(
        &self,
        py: Python<'py>,
        path: &str,
        config: &Bound<'py, PyAny>,
    ) -> PyResult<(bool, Bound<'py, PyAny>)> {
        let _in_flight = self.in_flight.enter()?;
        validate_path(path)?;
        // Both constructors own their storage and expose it without another tree conversion.
        let parsed;
        let encoded;
        let config = if let Ok(json) = config.cast_exact::<PyBytes>() {
            parsed = WafObject::from_json(json.as_bytes())
                .ok_or_else(|| PyValueError::new_err("invalid WAF configuration JSON"))?;
            &*parsed
        } else {
            encoded = Encoder::new(py, Limits::UNLIMITED).encode(config)?;
            &encoded
        };
        let mut diagnostics = WafOwnedDefaultAllocator::<WafMap>::default();
        let accepted = py.detach(|| {
            // Empty paths are rejected by the C API; avoid the wrapper's debug assertion.
            if path.is_empty() {
                return false;
            }
            lock(&self.raw).add_or_update_config(path, config, Some(&mut diagnostics))
        });
        Ok((accepted, decode(py, diagnostics.as_object())?))
    }

    fn remove_config(&self, py: Python<'_>, path: &str) -> PyResult<bool> {
        let _in_flight = self.in_flight.enter()?;
        validate_path(path)?;
        Ok(py.detach(|| lock(&self.raw).remove_config(path)))
    }

    #[pyo3(signature = (filter_str=None))]
    fn config_paths_count(&self, py: Python<'_>, filter_str: Option<&str>) -> PyResult<u32> {
        let _in_flight = self.in_flight.enter()?;
        if let Some(filter) = filter_str {
            validate_path(filter)?;
        }
        Ok(py.detach(|| lock(&self.raw).config_paths_count(filter_str)))
    }

    fn build(&self, py: Python<'_>) -> PyResult<Option<Handle>> {
        let _in_flight = self.in_flight.enter()?;
        Ok(py.detach(|| lock(&self.raw).build()).map(|raw| Handle {
            inner: Arc::new(raw),
        }))
    }
}

#[pyclass(frozen, module = "ddtrace.internal.native._native.ddwaf")]
struct Handle {
    inner: Arc<libddwaf::Handle>,
}

#[pymethods]
impl Handle {
    fn known_addresses(&self) -> Vec<String> {
        self.inner
            .known_addresses()
            .iter()
            .map(|s| s.to_string_lossy().into_owned())
            .collect()
    }

    fn context(&self) -> Option<Context> {
        self.inner
            .try_new_context()
            .map(|raw| Context::new(Target::Request(raw), Arc::clone(&self.inner)))
    }
}

enum Target {
    Request(libddwaf::Context),
    Operation(libddwaf::Subcontext),
}

struct ContextState {
    target: Mutex<Target>,
    // Keep the immutable rules generation alive through every context and subcontext.
    handle: Arc<libddwaf::Handle>,
}

#[pyclass(frozen, module = "ddtrace.internal.native._native.ddwaf")]
struct Context {
    inner: ProcessOwned<ContextState>,
}

impl Context {
    fn new(target: Target, handle: Arc<libddwaf::Handle>) -> Self {
        Self {
            inner: ProcessOwned::new(ContextState {
                target: Mutex::new(target),
                handle,
            }),
        }
    }
}

#[pymethods]
impl Context {
    fn subcontext(&self, py: Python<'_>) -> PyResult<Option<Context>> {
        let ctx = self.inner.get()?;
        let target = py.detach(|| match &*lock(&ctx.target) {
            Target::Request(parent) => parent
                .new_subcontext()
                .map(Some)
                .map_err(|e| PyRuntimeError::new_err(e.to_string())),
            Target::Operation(_) => Err(PyValueError::new_err("subcontexts cannot be nested")),
        })?;
        Ok(target.map(|raw| Context::new(Target::Operation(raw), Arc::clone(&ctx.handle))))
    }

    fn run<'py>(
        &self,
        py: Python<'py>,
        data: &Bound<'py, PyAny>,
        timeout_us: u64,
    ) -> PyResult<Bound<'py, PyTuple>> {
        let ctx = self.inner.get()?;
        let mut encoder = Encoder::new(py, Limits::DEFAULT);
        let input = encoder.encode(data)?;
        let result = if let Ok(map) = WafMap::try_from(input) {
            py.detach(|| {
                let mut target = lock(&ctx.target);
                let timeout = Duration::from_micros(timeout_us);
                match &mut *target {
                    Target::Request(context) => context.run(map, timeout),
                    Target::Operation(context) => context.run(map, timeout),
                }
            })
        } else {
            Err(RunError::InvalidObject)
        };
        let (code, output) = match result {
            Ok(RunResult::NoMatch(output)) => (0, Some(output)),
            Ok(RunResult::Match(output)) => (1, Some(output)),
            Err(RunError::InvalidObject) => (-2, None),
            Err(RunError::InvalidArgument) => (-1, None),
            Err(_) => (-3, None),
        };
        let (events, actions, duration, timeout, attributes, keep) =
            if let Some(ref output) = output {
                let events = output
                    .events()
                    .map(|v| decode(py, v.value().as_object()))
                    .transpose()?
                    .unwrap_or_else(|| PyList::empty(py).into_any());
                let actions = output
                    .actions()
                    .map(|v| decode(py, v.value().as_object()))
                    .transpose()?
                    .unwrap_or_else(|| PyDict::new(py).into_any());
                let attributes = output
                    .attributes()
                    .map(|v| decode(py, v.value().as_object()))
                    .transpose()?
                    .unwrap_or_else(|| PyDict::new(py).into_any());
                (
                    Some(events),
                    Some(actions),
                    output.duration().as_nanos() as u64,
                    output.timeout(),
                    Some(attributes),
                    output.keep(),
                )
            } else {
                (None, None, 0, false, None, false)
            };
        let (string_length, container_size, container_depth) = encoder.truncation.into_tuple();
        (
            code,
            events,
            actions,
            duration,
            timeout,
            attributes,
            keep,
            string_length,
            container_size,
            container_depth,
        )
            .into_pyobject(py)
    }
}

#[pyfunction]
#[pyo3(signature = (data, max_objects=256, max_depth=20, max_string_length=4096))]
fn _encode<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    max_objects: usize,
    max_depth: i64,
    max_string_length: usize,
) -> PyResult<(Bound<'py, PyAny>, Truncations)> {
    checked_version()?;
    let mut encoder = Encoder::new(
        py,
        Limits {
            objects: max_objects,
            depth: max_depth,
            string: max_string_length.min(u32::MAX as usize),
        },
    );
    let obj = encoder.encode(data)?;
    Ok((describe(py, &obj)?, encoder.truncation.into_tuple()))
}

#[pymodule]
pub fn ddwaf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(_encode, m)?)?;
    m.add_class::<Builder>()?;
    m.add_class::<Handle>()?;
    m.add_class::<Context>()?;
    Ok(())
}
