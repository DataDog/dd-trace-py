//! AppSec libddwaf evaluation boundary.
//!
//! One coarse call per evaluation: Python input is encoded into native storage while attached,
//! the target lock is taken and libddwaf runs while detached from the interpreter, and the
//! native result is decoded into Python values after the lock is released.
//!
//! libddwaf is loaded lazily by `load`; registering this module never touches it, so a missing
//! or incompatible library only disables AppSec.

pub mod abi;
mod convert;

use abi::*;
use convert::{decode, decode_result, describe, Encoder, Limits, Owned, Truncations};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString, PyTuple};
use std::ffi::{c_char, c_void, CStr};
use std::mem::ManuallyDrop;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

fn api() -> PyResult<Arc<Api>> {
    Api::get().ok_or_else(|| PyRuntimeError::new_err("libddwaf is not loaded"))
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

/// Moves raw native pointers into a detached closure. Access goes through `get` so that the
/// closure captures the wrapper, not its (non-Send) field.
struct Detached<T>(T);
// SAFETY: the wrapped pointers are only used by libddwaf calls serialized by our own locks.
unsafe impl<T> Send for Detached<T> {}
unsafe impl<T> Sync for Detached<T> {}
impl<T: Copy> Detached<T> {
    #[inline(always)]
    fn get(&self) -> T {
        self.0
    }
}

#[inline]
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    // No user code runs under these locks, so poisoning carries no broken invariant.
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Loads libddwaf from the first loadable candidate; returns (loaded path, version).
#[pyfunction]
fn load(candidates: Vec<String>) -> PyResult<(String, String)> {
    let api = Api::load(&candidates).map_err(LoadError::into_py_err)?;
    Ok((api.loaded_candidate.clone(), api.version.clone()))
}

struct RawBuilder {
    ptr: ddwaf_builder,
}
// SAFETY: the builder pointer is only used under the `Builder` mutex.
unsafe impl Send for RawBuilder {}

#[pyclass(frozen, module = "ddtrace.internal.native._native.ddwaf")]
struct Builder {
    raw: Mutex<RawBuilder>,
    in_flight: InFlight,
    api: Arc<Api>,
}

impl Drop for Builder {
    fn drop(&mut self) {
        if self.in_flight.can_drop() {
            if let Ok(raw) = self.raw.get_mut() {
                unsafe { (self.api.ddwaf_builder_destroy)(raw.ptr) };
            }
        }
    }
}

fn path_len(path: &str) -> PyResult<u32> {
    u32::try_from(path.len()).map_err(|_| PyValueError::new_err("configuration path is too long"))
}

#[pymethods]
impl Builder {
    #[new]
    fn new() -> PyResult<Self> {
        let api = api()?;
        let ptr = unsafe { (api.ddwaf_builder_init)() };
        if ptr.is_null() {
            return Err(PyRuntimeError::new_err("ddwaf_builder_init failed"));
        }
        Ok(Self {
            raw: Mutex::new(RawBuilder { ptr }),
            in_flight: InFlight::new(),
            api,
        })
    }

    /// Adds or replaces the configuration at `path`, from JSON bytes or Python values.
    /// Returns (accepted, diagnostics).
    fn add_or_update_config<'py>(
        &self,
        py: Python<'py>,
        path: &str,
        config: &Bound<'py, PyAny>,
    ) -> PyResult<(bool, Bound<'py, PyAny>)> {
        let _in_flight = self.in_flight.enter()?;
        let api = &*self.api;
        let path_len = path_len(path)?;
        let config = if let Ok(json) = config.cast_exact::<PyBytes>() {
            let json = json.as_bytes();
            let len = u32::try_from(json.len())
                .map_err(|_| PyValueError::new_err("WAF configuration is too large"))?;
            let mut obj = ddwaf_object::invalid();
            let ok = unsafe {
                (api.ddwaf_object_from_json)(
                    &mut obj,
                    json.as_ptr().cast(),
                    len,
                    api.default_allocator,
                )
            };
            let obj = Owned::new(api, obj);
            if !ok {
                return Err(PyValueError::new_err("invalid WAF configuration JSON"));
            }
            obj
        } else {
            Owned::new(
                api,
                Encoder::new(api, py, Limits::UNLIMITED).encode(config)?,
            )
        };
        let mut diagnostics = Owned::new(api, ddwaf_object::invalid());
        let args = Detached((
            &config.obj as *const ddwaf_object,
            &mut diagnostics.obj as *mut ddwaf_object,
        ));
        let accepted = py.detach(|| {
            let (config, diagnostics) = args.get();
            let builder = lock(&self.raw);
            unsafe {
                (api.ddwaf_builder_add_or_update_config)(
                    builder.ptr,
                    path.as_ptr().cast::<c_char>(),
                    path_len,
                    config,
                    diagnostics,
                )
            }
        });
        let diagnostics = unsafe { decode(py, api, &diagnostics.obj) }?;
        Ok((accepted, diagnostics))
    }

    fn remove_config(&self, py: Python<'_>, path: &str) -> PyResult<bool> {
        let _in_flight = self.in_flight.enter()?;
        let path_len = path_len(path)?;
        Ok(py.detach(|| unsafe {
            (self.api.ddwaf_builder_remove_config)(
                lock(&self.raw).ptr,
                path.as_ptr().cast::<c_char>(),
                path_len,
            )
        }))
    }

    #[pyo3(signature = (filter_str=None))]
    fn config_paths_count(&self, py: Python<'_>, filter_str: Option<&str>) -> PyResult<u32> {
        let _in_flight = self.in_flight.enter()?;
        let (ptr, len) = match filter_str {
            Some(filter) => (filter.as_ptr().cast::<c_char>(), path_len(filter)?),
            None => (std::ptr::null(), 0),
        };
        let filter = Detached(ptr);
        Ok(py.detach(|| unsafe {
            (self.api.ddwaf_builder_get_config_paths)(
                lock(&self.raw).ptr,
                std::ptr::null_mut(),
                filter.get(),
                len,
            )
        }))
    }

    /// Builds an immutable WAF instance from the current configurations, or None.
    fn build(&self, py: Python<'_>) -> PyResult<Option<Handle>> {
        let _in_flight = self.in_flight.enter()?;
        let raw = py
            .detach(|| {
                Detached(unsafe { (self.api.ddwaf_builder_build_instance)(lock(&self.raw).ptr) })
            })
            .0;
        if raw.is_null() {
            return Ok(None);
        }
        Ok(Some(Handle {
            inner: Arc::new(HandleInner {
                raw,
                api: Arc::clone(&self.api),
            }),
        }))
    }
}

struct HandleInner {
    raw: ddwaf_handle,
    api: Arc<Api>,
}
// SAFETY: a built WAF instance is immutable; contexts can be created from it concurrently.
unsafe impl Send for HandleInner {}
unsafe impl Sync for HandleInner {}

impl Drop for HandleInner {
    fn drop(&mut self) {
        unsafe { (self.api.ddwaf_destroy)(self.raw) }
    }
}

#[pyclass(frozen, module = "ddtrace.internal.native._native.ddwaf")]
struct Handle {
    inner: Arc<HandleInner>,
}

#[pymethods]
impl Handle {
    fn known_addresses<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let inner = &*self.inner;
        let mut size = 0u32;
        let addresses = unsafe { (inner.api.ddwaf_known_addresses)(inner.raw, &mut size) };
        let list = PyList::empty(py);
        if !addresses.is_null() {
            for i in 0..size as usize {
                let address = unsafe { CStr::from_ptr(*addresses.add(i)) };
                list.append(PyString::new(py, &address.to_string_lossy()))?;
            }
        }
        Ok(list)
    }

    /// Creates a request context, or None on failure.
    fn context(&self) -> PyResult<Option<Context>> {
        let inner = &*self.inner;
        let api = &inner.api;
        let raw = unsafe { (api.ddwaf_context_init)(inner.raw, api.default_allocator) };
        Ok((!raw.is_null()).then(|| Context::new(raw, false, Arc::clone(&self.inner))))
    }
}

struct RawContext {
    raw: *mut c_void,
    subcontext: bool,
    lock: Mutex<()>,
    // Keeps the WAF instance (and with it the loaded library) alive for the target's lifetime.
    handle: Arc<HandleInner>,
}
// SAFETY: `raw` is only used under `lock`, or in `drop` once no other reference exists.
unsafe impl Send for RawContext {}
unsafe impl Sync for RawContext {}

impl Drop for RawContext {
    fn drop(&mut self) {
        let api = &self.handle.api;
        unsafe {
            if self.subcontext {
                (api.ddwaf_subcontext_destroy)(self.raw)
            } else {
                (api.ddwaf_context_destroy)(self.raw)
            }
        }
    }
}

/// A context or a subcontext. Each has its own lock: libddwaf forbids concurrent use of one
/// target, while distinct targets (including a parent and its subcontexts) may run in parallel.
#[pyclass(frozen, module = "ddtrace.internal.native._native.ddwaf")]
struct Context {
    inner: ProcessOwned<RawContext>,
}

impl Context {
    fn new(raw: *mut c_void, subcontext: bool, handle: Arc<HandleInner>) -> Self {
        Self {
            inner: ProcessOwned::new(RawContext {
                raw,
                subcontext,
                lock: Mutex::new(()),
                handle,
            }),
        }
    }
}

#[pymethods]
impl Context {
    /// Derives a subcontext for non-persisting (RASP) data, or None on failure. libddwaf 2.x
    /// subcontexts share the parent's data by reference count and may outlive it.
    fn subcontext(&self, py: Python<'_>) -> PyResult<Option<Context>> {
        let ctx = self.inner.get()?;
        if ctx.subcontext {
            return Err(PyValueError::new_err("subcontexts cannot be nested"));
        }
        let parent = Detached(ctx.raw);
        let api = &ctx.handle.api;
        let raw = py
            .detach(|| {
                let _guard = lock(&ctx.lock);
                Detached(unsafe { (api.ddwaf_subcontext_init)(parent.get()) })
            })
            .get();
        Ok((!raw.is_null()).then(|| Context::new(raw, true, Arc::clone(&ctx.handle))))
    }

    /// Evaluates `data` (an address map) with a timeout in microseconds.
    ///
    /// Returns (return_code, events, actions, duration_ns, timeout, attributes, keep,
    /// truncated_string_length, truncated_container_size, truncated_container_depth).
    /// The result fields are None when libddwaf produced no result.
    fn run<'py>(
        &self,
        py: Python<'py>,
        data: &Bound<'py, PyAny>,
        timeout_us: u64,
    ) -> PyResult<Bound<'py, PyTuple>> {
        let ctx = self.inner.get()?;
        let api = &*ctx.handle.api;
        let mut encoder = Encoder::new(api, py, Limits::DEFAULT);
        let mut input = encoder.encode(data)?;
        let mut result = ddwaf_object::invalid();
        let eval = if ctx.subcontext {
            api.ddwaf_subcontext_eval
        } else {
            api.ddwaf_context_eval
        };
        let args = Detached((
            ctx.raw,
            &mut input as *mut ddwaf_object,
            &mut result as *mut ddwaf_object,
        ));
        let code = py.detach(|| {
            let (target, input, result) = args.get();
            let _guard = lock(&ctx.lock);
            unsafe { eval(target, input, api.default_allocator, result, timeout_us) }
        });
        // Input ownership (ddwaf.h): OK/MATCH -> retained by the target until it is destroyed;
        // INVALID_OBJECT -> already freed; INVALID_ARGUMENT -> not taken, free it here;
        // INTERNAL -> unknown, so leave it alone rather than risk a double free.
        if code == DDWAF_ERR_INVALID_ARGUMENT {
            drop(Owned::new(api, input));
        }
        let result = Owned::new(api, result);
        let output = if code == DDWAF_ERR_INTERNAL {
            None
        } else {
            unsafe { decode_result(py, api, &result.obj) }?
        };
        drop(result);
        let (string_length, container_size, container_depth) = encoder.truncation.into_tuple();
        let (events, actions, duration_ns, timeout, attributes, keep) = match output {
            Some(o) => (
                Some(o.events),
                Some(o.actions),
                o.duration_ns,
                o.timeout,
                Some(o.attributes),
                o.keep,
            ),
            None => (None, None, 0, false, None, false),
        };
        (
            code,
            events,
            actions,
            duration_ns,
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

/// Encodes `data` and returns its raw native view and truncations, then frees it. Test hook for
/// differential checks against the ctypes serializer.
#[pyfunction]
#[pyo3(signature = (data, max_objects=256, max_depth=20, max_string_length=4096))]
fn _encode<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    max_objects: usize,
    max_depth: i64,
    max_string_length: usize,
) -> PyResult<(Bound<'py, PyAny>, Truncations)> {
    let api = api()?;
    let limits = Limits {
        objects: max_objects,
        depth: max_depth,
        string: max_string_length.min(u32::MAX as usize),
    };
    let mut encoder = Encoder::new(&api, py, limits);
    let obj = Owned::new(&api, encoder.encode(data)?);
    let view = unsafe { describe(py, &api, &obj.obj) }?;
    Ok((view, encoder.truncation.into_tuple()))
}

#[pymodule]
pub fn ddwaf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(load, m)?)?;
    m.add_function(wrap_pyfunction!(_encode, m)?)?;
    m.add_class::<Builder>()?;
    m.add_class::<Handle>()?;
    m.add_class::<Context>()?;
    Ok(())
}
