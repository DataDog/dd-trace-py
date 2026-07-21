use pyo3::prelude::*;
use pyo3::types::PyTuple;
use pyo3::{intern, Bound, Py, PyAny, PyResult, Python};

use crate::contextvar::{contextvar_get, safe_contextvar_set};
use crate::event_hub::dispatch as event_dispatch;
use crate::event_hub::has_listeners;

/// Native base for `ddtrace._trace.provider.DefaultContextProvider`.
///
/// Incremental migration (mirrors the Python API so the existing test suite applies
/// unchanged): the hot, read-mostly `active()` path lives here; `activate()` and the
/// rarely-taken `_update_active()` reconciliation walk stay in Python for now and are
/// reached via normal attribute resolution on the Python subclass.
///
/// `contextvar`/`span_type` are stored as opaque Python handles (never read from Rust
/// as native types) so we pay no conversion cost. Both are `Option` so a provider subclass
/// (tornado/ci_visibility/llmobs) that constructs via `BaseContextProvider.__init__` and
/// never populates these is represented as `None` (unconfigured).
#[pyo3::pyclass(
    name = "DefaultContextProvider",
    module = "ddtrace.internal._native",
    subclass
)]
pub struct DefaultContextProvider {
    /// The `_DD_CONTEXTVAR` object (a `contextvars.ContextVar`), or `None` when a subclass
    /// constructs without supplying it. Stored as an opaque handle — never read from Rust
    /// as a native type.
    contextvar: Option<Py<PyAny>>,
    /// The Python `Span` class, used for the exact-type check. `None` when unset.
    span_type: Option<Py<PyAny>>,
}

#[pyo3::pymethods]
impl DefaultContextProvider {
    #[new]
    #[pyo3(signature = (contextvar=None, span_type=None))]
    fn new(contextvar: Option<Py<PyAny>>, span_type: Option<Py<PyAny>>) -> Self {
        DefaultContextProvider {
            contextvar,
            span_type,
        }
    }

    /// Set the contextvar + Span type after construction. PyO3 classes construct via
    /// `__new__`, so a Python subclass cannot forward these through `super().__init__`;
    /// it calls this from its own `__init__` instead.
    fn _configure(&mut self, contextvar: Py<PyAny>, span_type: Py<PyAny>) {
        self.contextvar = Some(contextvar);
        self.span_type = Some(span_type);
    }

    /// Exposed so Python (e.g. the gevent integration) and subclasses keep access to the
    /// underlying contextvar object. Returns Python `None` when unconfigured.
    #[getter]
    fn _contextvar(&self, py: Python<'_>) -> Py<PyAny> {
        self.contextvar
            .as_ref()
            .map_or_else(|| py.None(), |v| v.clone_ref(py))
    }

    fn _has_active_context(&self, py: Python<'_>) -> PyResult<bool> {
        let var = match &self.contextvar {
            Some(v) => v.bind(py),
            None => return Ok(false),
        };
        Ok(!contextvar_get(py, var)?.is_none())
    }

    /// Make the given context (`None | Span | Context`) active in the current execution,
    /// then emit `ddtrace.context_provider.activate` (listened to only by the profiler's
    /// stack collector). Mirrors the previous Python `activate` + `_activate_contextvar`.
    ///
    /// PERF: `activate` runs ~2x per span (activate child on start, re-activate parent on
    /// finish). The activate event has a listener only when the profiler is enabled, so in
    /// the common case skip the args-tuple entirely — a `PyTuple` is a GC-tracked container,
    /// so building one per activation adds gen0 GC pressure on a very hot path. `has_listeners`
    /// is a cheap locked hashmap probe; gate the tuple + dispatch on it.
    ///
    /// NOTE: uses the crash-safe set unconditionally. On CPython >=3.12 a plain
    /// `PyContextVar_Set` is safe and slightly cheaper; branching on version is a follow-up
    /// (the perf-harness runs 3.10, which takes the safe path either way).
    fn activate(&self, py: Python<'_>, ctx: &Bound<'_, PyAny>) -> PyResult<()> {
        let var = match &self.contextvar {
            Some(v) => v.bind(py),
            // Unconfigured native base (a subclass that overrides activate); no-op.
            None => return Ok(()),
        };
        safe_contextvar_set(py, var, ctx)?;
        if has_listeners("ddtrace.context_provider.activate") {
            let args = PyTuple::new(py, [ctx])?;
            event_dispatch(
                py,
                "ddtrace.context_provider.activate",
                Some(args.into_any().unbind()),
                false,
            )?;
        }
        Ok(())
    }

    /// Return the active span or context for the current execution.
    ///
    /// The active value is `None | Span | Context`, so it is handled purely as `PyAny`.
    /// Fast path (the overwhelmingly common case): a live Span (`duration_ns is None`) or a
    /// Context/None is returned directly with no walk. Only a *finished* Span (deactivation
    /// reconciliation) drops to Python `_update_active`.
    fn active<'py>(slf: &Bound<'py, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        // Read everything that needs the Rust borrow up front, then release it before any
        // reentrant Python call (getattr / _update_active) that could re-borrow `slf`.
        let item;
        let is_span;
        {
            let this = slf.borrow();
            let var = match &this.contextvar {
                Some(v) => v.bind(py),
                None => return Ok(py.None().into_bound(py)),
            };
            item = contextvar_get(py, var)?;
            if item.is_none() {
                return Ok(item);
            }
            // `type(item) is Span` — exact-type identity via pointer compare (no isinstance,
            // no coercion). A Context or None fails this and is returned as-is, matching the
            // Python provider.
            is_span = match &this.span_type {
                Some(span_type) => item.get_type().as_ptr() == span_type.as_ptr(),
                None => false,
            };
        }
        if is_span {
            if item.getattr(intern!(py, "duration_ns"))?.is_none() {
                // live span: fast path (the overwhelmingly common case)
                return Ok(item);
            }
            // finished span: hand the parent-chain reconciliation to Python.
            return slf
                .as_any()
                .call_method1(intern!(py, "_update_active"), (item,));
        }
        Ok(item)
    }

    /// Reconcile the active trace after a span finishes: walk up `_parent` past finished
    /// spans to the nearest live ancestor (re-activating it), or restore a reactivatable
    /// parent context. Rarely taken (only when the contextvar holds a *finished* span).
    /// `_parent`/`_parent_context`/`_reactivate` are Python slots read via getattr; the
    /// walk is almost always a single step (a parent that finished before its child is a
    /// programming error).
    #[pyo3(name = "_update_active")]
    fn update_active<'py>(
        &self,
        py: Python<'py>,
        span: Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let mut new_active = span.clone();
        loop {
            if new_active.is_none() {
                break;
            }
            // while new_active.duration_ns is not None  (i.e. it's finished)
            if new_active.getattr(intern!(py, "duration_ns"))?.is_none() {
                break;
            }
            let parent = new_active.getattr(intern!(py, "_parent"))?;
            if parent.is_none() {
                let pctx = new_active.getattr(intern!(py, "_parent_context"))?;
                if !pctx.is_none() && pctx.getattr(intern!(py, "_reactivate"))?.is_truthy()? {
                    self.activate(py, &pctx)?;
                    return Ok(pctx);
                }
            }
            new_active = parent;
        }
        if new_active.as_ptr() != span.as_ptr() {
            self.activate(py, &new_active)?;
        }
        Ok(new_active)
    }

    fn __call__<'py>(slf: &Bound<'py, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        Self::active(slf, py)
    }

    /// PyO3 does not auto-derive the cyclic-GC protocol; this pyclass holds `Py<PyAny>`
    /// fields, so it must visit them. The provider is a long-lived singleton whose fields
    /// (a `ContextVar` and the `Span` class) are effectively atomic and never close a cycle,
    /// but traversal is still required for correct refcount accounting (and to satisfy the
    /// `pyclass-missing-traverse` lint). See `src/native/span/span_data.rs`.
    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let Some(v) = &self.contextvar {
            visit.call(v)?;
        }
        if let Some(t) = &self.span_type {
            visit.call(t)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        // Drop owned Python references so CPython can break any cycle through this object.
        self.contextvar = None;
        self.span_type = None;
    }
}

pub fn register_context_provider(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DefaultContextProvider>()?;
    Ok(())
}
