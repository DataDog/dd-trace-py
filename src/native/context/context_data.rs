use pyo3::{
    types::{PyAny, PyAnyMethods as _, PyDict, PyList, PyTuple},
    Bound, Py, Python,
};

/// Native storage layer for `ddtrace._trace.context.Context`.
#[pyo3::pyclass(name = "ContextData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct ContextData {
    pub trace_id: Option<u128>,
    pub span_id: Option<u128>,
    /// dict[str, str]. `None` only in the brief window before `__new__` finishes
    /// or after `__clear__` runs during GC teardown -- getters lazily
    /// re-materialize an empty dict, mirroring `SpanData::meta_struct`.
    meta: Option<Py<PyDict>>,
    /// dict[str, NumericType].
    metrics: Option<Py<PyDict>>,
    /// dict[str, Any].
    baggage: Option<Py<PyDict>>,
    /// list[SpanLink].
    span_links: Option<Py<PyList>>,
    /// Optional ddtrace.internal.otel_sampling.OtelSamplingState shared by all
    /// per-span Context copies belonging to the same trace.
    otel_sampling_state: Option<Py<PyAny>>,
    #[pyo3(get, set, name = "_is_remote")]
    pub is_remote: bool,
    #[pyo3(get, set, name = "_reactivate")]
    pub reactivate: bool,
}

#[pyo3::pymethods]
impl ContextData {
    #[new]
    #[allow(unused_variables)]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        trace_id=None,
        span_id=None,
        dd_origin=None,         // placeholder for Context.__init__, handled there
        sampling_priority=None, // placeholder for Context.__init__, handled there
        meta=None,
        metrics=None,
        lock=None,              // placeholder for Context.__init__, handled there
        span_links=None,
        baggage=None,
        is_remote=true,
        otel_sampling_state=None,
        *args,
        **kwargs
    ))]
    pub fn __new__<'p>(
        py: Python<'p>,
        trace_id: Option<&Bound<'p, PyAny>>,
        span_id: Option<&Bound<'p, PyAny>>,
        dd_origin: Option<&Bound<'p, PyAny>>, // placeholder, not used
        sampling_priority: Option<&Bound<'p, PyAny>>, // placeholder, not used
        meta: Option<&Bound<'p, PyDict>>,
        metrics: Option<&Bound<'p, PyDict>>,
        lock: Option<&Bound<'p, PyAny>>, // placeholder, not used
        span_links: Option<&Bound<'p, PyList>>,
        baggage: Option<&Bound<'p, PyDict>>,
        is_remote: bool,
        otel_sampling_state: Option<&Bound<'p, PyAny>>,
        // Accept *args/**kwargs so subclasses don't need to override __new__
        args: &Bound<'p, PyTuple>,
        kwargs: Option<&Bound<'p, PyDict>>,
    ) -> Self {
        Self {
            trace_id: trace_id.and_then(|v| v.extract::<u128>().ok()),
            span_id: span_id.and_then(|v| v.extract::<u128>().ok()),
            meta: Some(
                meta.map(|d| d.clone().unbind())
                    .unwrap_or_else(|| PyDict::new(py).unbind()),
            ),
            metrics: Some(
                metrics
                    .map(|d| d.clone().unbind())
                    .unwrap_or_else(|| PyDict::new(py).unbind()),
            ),
            baggage: Some(
                baggage
                    .map(|d| d.clone().unbind())
                    .unwrap_or_else(|| PyDict::new(py).unbind()),
            ),
            span_links: Some(
                span_links
                    .map(|l| l.clone().unbind())
                    .unwrap_or_else(|| PyList::empty(py).unbind()),
            ),
            otel_sampling_state: otel_sampling_state.map(|state| state.clone().unbind()),
            is_remote,
            reactivate: false,
        }
    }

    // --- trace_id ---
    #[getter]
    #[inline(always)]
    fn get_trace_id(&self) -> Option<u128> {
        self.trace_id
    }

    #[setter]
    #[inline(always)]
    fn set_trace_id(&mut self, value: Option<&Bound<'_, PyAny>>) {
        match value {
            None => self.trace_id = None,
            // Silently ignore invalid types (keep existing value), matching SpanData's setters.
            Some(v) => {
                if let Ok(id) = v.extract::<u128>() {
                    self.trace_id = Some(id);
                }
            }
        }
    }

    // --- span_id ---
    #[getter]
    #[inline(always)]
    fn get_span_id(&self) -> Option<u128> {
        self.span_id
    }

    #[setter]
    #[inline(always)]
    fn set_span_id(&mut self, value: Option<&Bound<'_, PyAny>>) {
        match value {
            None => self.span_id = None,
            Some(v) => {
                if let Ok(id) = v.extract::<u128>() {
                    self.span_id = Some(id);
                } else if let Ok(s) = v.extract::<&str>() {
                    if let Ok(id) = s.parse::<u128>() {
                        self.span_id = Some(id);
                    }
                }
            }
        }
    }

    // --- _meta ---
    #[getter(_meta)]
    #[inline(always)]
    fn get_meta<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.meta
            .get_or_insert_with(|| PyDict::new(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_meta)]
    #[inline(always)]
    fn set_meta(&mut self, value: &Bound<'_, PyDict>) {
        self.meta = Some(value.clone().unbind());
    }

    // --- _metrics ---
    #[getter(_metrics)]
    #[inline(always)]
    fn get_metrics<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.metrics
            .get_or_insert_with(|| PyDict::new(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_metrics)]
    #[inline(always)]
    fn set_metrics(&mut self, value: &Bound<'_, PyDict>) {
        self.metrics = Some(value.clone().unbind());
    }

    // --- _baggage ---
    #[getter(_baggage)]
    #[inline(always)]
    fn get_baggage<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.baggage
            .get_or_insert_with(|| PyDict::new(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_baggage)]
    #[inline(always)]
    fn set_baggage(&mut self, value: &Bound<'_, PyDict>) {
        self.baggage = Some(value.clone().unbind());
    }

    // --- _span_links ---
    #[getter(_span_links)]
    #[inline(always)]
    fn get_span_links<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyList> {
        self.span_links
            .get_or_insert_with(|| PyList::empty(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_span_links)]
    #[inline(always)]
    fn set_span_links(&mut self, value: &Bound<'_, PyList>) {
        self.span_links = Some(value.clone().unbind());
    }

    // --- _otel_sampling_state_data ---
    #[getter(_otel_sampling_state_data)]
    #[inline(always)]
    fn get_otel_sampling_state<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyAny>> {
        self.otel_sampling_state
            .as_ref()
            .map(|state| state.bind(py).clone())
    }

    #[setter(_otel_sampling_state_data)]
    #[inline(always)]
    fn set_otel_sampling_state(&mut self, value: Option<&Bound<'_, PyAny>>) {
        self.otel_sampling_state = value.map(|state| state.clone().unbind());
    }

    // --- Cyclic GC support ---
    //
    // `_meta`/`_metrics`/`_baggage`/`_span_links` and the OTel sampling state are
    // Python objects that can participate in reference cycles. Without
    // `__traverse__`/`__clear__` such a cycle is invisible to CPython's cyclic GC
    // and leaks forever -- see the identical rationale on `SpanData`, which hit
    // exactly this class of bug for `meta_struct`.
    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let Some(d) = &self.meta {
            visit.call(d)?;
        }
        if let Some(d) = &self.metrics {
            visit.call(d)?;
        }
        if let Some(d) = &self.baggage {
            visit.call(d)?;
        }
        if let Some(l) = &self.span_links {
            visit.call(l)?;
        }
        if let Some(state) = &self.otel_sampling_state {
            visit.call(state)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        // Reset to Default to drop every owned Python reference so CPython can
        // break cycles. See `SpanData::__clear__` for the identical rationale.
        *self = Self::default();
    }
}
