//! Native port of `ddtrace._trace.provider`.
//!
//! `BaseContextProvider`/`DefaultContextProvider` are the hot path for every
//! `tracer.context_provider.active()`/`.activate()` call. `_update_active` in
//! particular runs on every `active()` call while a span is active, so it
//! downcasts straight to `SpanData` and reads `duration`, `_parent`, and
//! `_parent_context` as native fields instead of round-tripping through Python
//! attribute lookups.
//!
//! `_reactivate` is read from the parent `Context` via `getattr` because
//! `Context` is still a pure-Python class (porting it to native is a separate
//! effort — it carries substantial logic and many Python imports).

use std::sync::OnceLock;

use pyo3::{
    exceptions::{PyNotImplementedError, PyTypeError},
    types::{PyAnyMethods as _, PyDict, PyModule, PyModuleMethods as _, PyTuple},
    Bound, Py, PyAny, PyResult, Python,
};

use crate::contextvar::{contextvar_get, contextvar_new, safe_contextvar_set};
use crate::event_hub;
use crate::span::SpanData;

// The active context/span is tracked in a single process-wide `ContextVar`,
// shared by every `DefaultContextProvider` instance -- this mirrors the
// module-level `_DD_CONTEXTVAR` singleton in the old Python implementation.
static CONTEXTVAR: OnceLock<Py<PyAny>> = OnceLock::new();

/// Borrows the shared contextvar without touching its refcount -- `Py::bind`
/// is a pointer cast, not an INCREF, so the common (post-init) path here is
/// free. Only the one-time init path allocates.
fn contextvar(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    if let Some(v) = CONTEXTVAR.get() {
        return Ok(v.bind(py));
    }
    let cv = contextvar_new(py, "datadog_contextvar", &py.None().into_bound(py))?;
    // Lost races just drop their extra ContextVar; the getter always reads
    // back through the OnceLock so every caller ends up on the same instance.
    let _ = CONTEXTVAR.set(cv.unbind());
    Ok(CONTEXTVAR.get().unwrap().bind(py))
}

/// Set the active contextvar, routing through the crash-safe setter on
/// CPython < 3.12 (see `contextvar::safe_contextvar_set`).
fn activate_contextvar(py: Python<'_>, ctx: Option<&Bound<'_, PyAny>>) -> PyResult<()> {
    let cv = contextvar(py)?;
    match ctx {
        Some(v) => safe_contextvar_set(py, cv, v),
        None => safe_contextvar_set(py, cv, &py.None().into_bound(py)),
    }
}

const ACTIVATE_EVENT: &str = "ddtrace.context_provider.activate";

/// `core.dispatch("ddtrace.context_provider.activate", (ctx,))` -- called
/// directly instead of importing `ddtrace.internal.core`, since that module's
/// `dispatch` is itself a re-export of `event_hub::dispatch`.
///
/// `activate()` runs on every span start/finish, but the only listener
/// (profiling's stack sampler) is rarely registered, so check first to skip
/// the tuple allocation on the common no-listener path.
fn dispatch_activate(py: Python<'_>, ctx: Option<&Bound<'_, PyAny>>) -> PyResult<()> {
    if !event_hub::has_listeners(ACTIVATE_EVENT) {
        return Ok(());
    }
    let value = match ctx {
        Some(v) => v.clone(),
        None => py.None().into_bound(py),
    };
    let args = PyTuple::new(py, [value])?;
    event_hub::dispatch(py, ACTIVATE_EVENT, Some(args.into_any().unbind()), false)
}

/// `Some(v)` unless `v` is Python `None`, matching the `Optional[...]` return
/// convention used throughout this module. Consumes `v` to avoid an incref.
#[inline]
fn none_or_unbind(v: Bound<'_, PyAny>) -> Option<Py<PyAny>> {
    if v.is_none() {
        None
    } else {
        Some(v.unbind())
    }
}

/// Borrowing sibling of [`none_or_unbind`] for when the caller still needs `v`:
/// `Some(clone)` unless `v` is Python `None`.
#[inline]
fn none_or_clone<'py>(v: &Bound<'py, PyAny>) -> Option<Bound<'py, PyAny>> {
    if v.is_none() {
        None
    } else {
        Some(v.clone())
    }
}

/// A ``ContextProvider`` is an interface that provides the blueprint
/// for a callable class, capable to retrieve the current active
/// ``Context`` instance. Context providers must inherit this class
/// and implement:
/// * the ``active`` method, that returns the current active ``Context``
/// * the ``activate`` method, that sets the current active ``Context``
#[pyo3::pyclass(subclass, module = "ddtrace.internal._native")]
pub struct BaseContextProvider;

#[pyo3::pymethods]
impl BaseContextProvider {
    #[new]
    fn new() -> PyResult<Self> {
        // Mirrors the old `abc.ABCMeta` behavior: this class only exists to be
        // subclassed (by `DefaultContextProvider`, which provides its own
        // `__new__` and so never reaches this one).
        Err(PyTypeError::new_err(
            "Can't instantiate abstract class BaseContextProvider without an implementation \
             for abstract methods '_has_active_context', 'active'",
        ))
    }

    fn _has_active_context(&self) -> PyResult<bool> {
        Err(PyNotImplementedError::new_err(()))
    }

    #[pyo3(signature = (ctx=None))]
    fn activate(&self, py: Python<'_>, ctx: Option<Bound<'_, PyAny>>) -> PyResult<()> {
        dispatch_activate(py, ctx.as_ref())
    }

    fn active(&self) -> PyResult<Option<Py<PyAny>>> {
        Err(PyNotImplementedError::new_err(()))
    }

    /// Method available for backward-compatibility. It proxies the call to
    /// ``self.active()`` and must not do anything more.
    #[pyo3(signature = (*_args, **_kwargs))]
    fn __call__(
        slf: &Bound<'_, Self>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        // Dispatched via `call_method0` (not a direct Rust call) so subclass
        // overrides of `active` -- e.g. CIContextProvider, TracerStackContext --
        // are honored.
        slf.call_method0("active").map(Bound::unbind)
    }
}

/// Context provider that retrieves contexts from a context variable.
///
/// It is suitable for synchronous programming and for asynchronous executors
/// that support contextvars.
#[pyo3::pyclass(extends = BaseContextProvider, subclass, module = "ddtrace.internal._native")]
pub struct DefaultContextProvider;

#[pyo3::pymethods]
impl DefaultContextProvider {
    #[new]
    fn new() -> (Self, BaseContextProvider) {
        (DefaultContextProvider, BaseContextProvider)
    }

    /// Returns whether there is an active context in the current execution.
    fn _has_active_context(&self, py: Python<'_>) -> PyResult<bool> {
        let item = contextvar_get(py, contextvar(py)?)?;
        Ok(!item.is_none())
    }

    /// Makes the given context active in the current execution.
    #[pyo3(signature = (ctx=None))]
    fn activate<'py>(
        slf: &Bound<'py, Self>,
        py: Python<'py>,
        ctx: Option<Bound<'py, PyAny>>,
    ) -> PyResult<()> {
        activate_contextvar(py, ctx.as_ref())?;
        // `super(DefaultContextProvider, self).activate(ctx)` -- calls
        // BaseContextProvider's dispatch directly (not via `call_method`,
        // which would just re-resolve back to this same override).
        let base = slf.as_super();
        BaseContextProvider::activate(&base.borrow(), py, ctx)
    }

    /// Returns the active span or context for the current execution.
    fn active<'py>(slf: &Bound<'py, Self>, py: Python<'py>) -> PyResult<Option<Py<PyAny>>> {
        let item = contextvar_get(py, contextvar(py)?)?;
        if item.is_none() {
            return Ok(None);
        }
        match item.cast::<SpanData>() {
            Ok(span) => {
                let span = span.clone();
                // Dispatched via `call_method1` so subclass overrides of
                // `_update_active` -- e.g. LLMObsContextProvider -- are honored.
                slf.call_method1("_update_active", (span,))
                    .map(none_or_unbind)
            }
            Err(_) => Ok(Some(item.unbind())),
        }
    }

    /// Updates the active trace in an executor.
    ///
    /// When a span finishes, the active span becomes its parent.
    /// If no parent exists and the context is reactivatable, that context is restored.
    fn _update_active<'py>(
        slf: &Bound<'py, Self>,
        py: Python<'py>,
        span: Bound<'py, PyAny>,
    ) -> PyResult<Option<Py<PyAny>>> {
        let original = span.clone();
        let mut current = span;
        loop {
            // PERF: read `duration`, `_parent`, and `_parent_context` straight
            // off the native SpanData fields in one borrow -- avoids three
            // Python attribute lookups per ancestor hop.
            let (parent, parent_context) = {
                let Ok(sd) = current.cast::<SpanData>() else {
                    break; // not a Span (e.g. None) -- stop walking parents
                };
                let sd = sd.borrow();
                if sd.duration.is_none() {
                    break; // unfinished span -- stop walking parents
                }
                (
                    sd._parent.as_ref().map(|p| p.bind(py).clone()),
                    sd._parent_context.as_ref().map(|c| c.bind(py).clone()),
                )
            };
            if parent.is_none() {
                if let Some(parent_context) = parent_context {
                    // `_reactivate` lives on the pure-Python Context -- still a getattr.
                    if parent_context.getattr("_reactivate")?.is_truthy()? {
                        // self.activate(...): a subclass (e.g. CIContextProvider) may
                        // override `activate` without overriding `_update_active`.
                        slf.call_method1("activate", (parent_context.clone(),))?;
                        return Ok(Some(parent_context.unbind()));
                    }
                }
            }
            // Advance to the parent; `None` ends the walk on the next iteration.
            current = parent.unwrap_or_else(|| py.None().into_bound(py));
        }
        if !current.is(&original) {
            // self.activate(...): same reason as above.
            slf.call_method1("activate", (none_or_clone(&current),))?;
        }
        Ok(none_or_unbind(current))
    }
}

pub fn register_context_provider(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<BaseContextProvider>()?;
    m.add_class::<DefaultContextProvider>()?;
    // Exposed so `ddtrace._trace.provider` (and downstream code like the
    // gevent monkeypatch) can keep importing `_DD_CONTEXTVAR` directly.
    let cv = contextvar(m.py())?;
    m.add("DD_CONTEXTVAR", cv)?;
    Ok(())
}
