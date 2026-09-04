use std::sync::OnceLock;

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use pyo3::prelude::FromPyObjectOwned;
use pyo3::{
    exceptions::PyValueError,
    types::{PyAny, PyAnyMethods as _, PyDict, PyDictMethods as _, PyList, PyTuple},
    Bound, FromPyObject, IntoPyObject, Py, PyErr, PyResult, Python,
};

const ORIGIN_KEY: &str = "_dd.origin";
const SAMPLING_PRIORITY_KEY: &str = "_sampling_priority_v1";
const USER_ID_KEY: &str = "_dd.p.usr.id";
const W3C_TRACEPARENT_KEY: &str = "traceparent";
const W3C_TRACESTATE_KEY: &str = "tracestate";
const MAX_UINT_64BITS: u128 = (1u128 << 64) - 1;

type ContextState<'py> = (
    Option<u128>,
    Option<u128>,
    Bound<'py, PyDict>,
    Bound<'py, PyDict>,
    Bound<'py, PyList>,
    Bound<'py, PyDict>,
    bool,
    bool,
    Option<f64>,
);

static W3C_GET_DD_LIST_MEMBER: OnceLock<Py<PyAny>> = OnceLock::new();
static W3C_BUILD_TRACESTATE_MEMBERS: OnceLock<Py<PyAny>> = OnceLock::new();
static NORMALIZE_OTEL_TRACESTATE: OnceLock<Py<PyAny>> = OnceLock::new();
static MATERIALIZE_OTEL_SAMPLING_DECISION: OnceLock<Py<PyAny>> = OnceLock::new();
static TRACESTATE_MAX_BYTES_CACHE: OnceLock<usize> = OnceLock::new();

fn cached_fn<'py>(
    py: Python<'py>,
    cache: &OnceLock<Py<PyAny>>,
    module: &str,
    name: &str,
) -> PyResult<Bound<'py, PyAny>> {
    if let Some(v) = cache.get() {
        return Ok(v.bind(py).clone());
    }
    let f = py.import(module)?.getattr(name)?;
    let _ = cache.set(f.clone().unbind());
    Ok(f)
}

fn cached_const<T>(py: Python<'_>, cache: &OnceLock<T>, module: &str, name: &str) -> PyResult<T>
where
    T: Copy + for<'py> FromPyObjectOwned<'py>,
    for<'a, 'py> PyErr: From<<T as FromPyObject<'a, 'py>>::Error>,
{
    if let Some(value) = cache.get() {
        return Ok(*value);
    }
    let value = py.import(module)?.getattr(name)?.extract()?;
    let _ = cache.set(value);
    Ok(value)
}

fn w3c_get_dd_list_member_fn(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    cached_fn(
        py,
        &W3C_GET_DD_LIST_MEMBER,
        "ddtrace.internal.utils.http",
        "w3c_get_dd_list_member",
    )
}

fn w3c_build_tracestate_members_fn(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    cached_fn(
        py,
        &W3C_BUILD_TRACESTATE_MEMBERS,
        "ddtrace.internal.utils.http",
        "w3c_build_tracestate_members",
    )
}

fn normalize_otel_tracestate_fn(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    cached_fn(
        py,
        &NORMALIZE_OTEL_TRACESTATE,
        "ddtrace.internal.opentelemetry.sampling",
        "normalize_otel_tracestate",
    )
}

fn materialize_otel_sampling_decision_fn(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    cached_fn(
        py,
        &MATERIALIZE_OTEL_SAMPLING_DECISION,
        "ddtrace.internal.opentelemetry.sampling",
        "materialize_otel_sampling_decision",
    )
}

fn dd_trace_tracestate_max_bytes(py: Python<'_>) -> PyResult<usize> {
    // Python owns the propagation limits. Resolve this lazily to avoid
    // making ddtrace.internal.constants import the native extension during startup.
    cached_const::<usize>(
        py,
        &TRACESTATE_MAX_BYTES_CACHE,
        "ddtrace.internal.constants",
        "DD_TRACE_TRACESTATE_MAX_BYTES",
    )
}

#[inline]
fn is_printable_ascii(s: &str) -> bool {
    s.chars().all(|c| (0x20..=0x7E).contains(&(c as u32)))
}

#[inline]
fn del_item_if_present(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<()> {
    if dict.contains(key)? {
        dict.del_item(key)?;
    }
    Ok(())
}

#[inline]
fn extract_trace_id(v: Option<&Bound<'_, PyAny>>) -> Option<u128> {
    v.and_then(|v| v.extract::<u128>().ok())
}

#[inline]
fn extract_span_id(v: Option<&Bound<'_, PyAny>>) -> Option<u128> {
    let v = v?;
    if let Ok(id) = v.extract::<u128>() {
        return Some(id);
    }
    v.extract::<&str>()
        .ok()
        .and_then(|s| s.parse::<u128>().ok())
}

#[pyo3::pyclass(name = "Context", module = "ddtrace._trace.context", weakref, subclass)]
pub struct Context {
    pub trace_id: Option<u128>,
    pub span_id: Option<u128>,
    meta: Py<PyDict>,
    metrics: Py<PyDict>,
    baggage: Option<Py<PyDict>>,
    span_links: Option<Py<PyList>>,
    #[pyo3(get, set, name = "_is_remote")]
    pub is_remote: bool,
    #[pyo3(get, set, name = "_reactivate")]
    pub reactivate: bool,
    // Child contexts point otel_sampling_state_owner at the trace's
    // owning Context. This keeps pending propagation state visible across copies
    // without allocating a holder or storing control data in meta/metrics.
    #[pyo3(get, set, name = "_otel_sampling_state_data")]
    pub otel_sampling_state_data: Option<f64>,
    #[pyo3(get, set, name = "_otel_sampling_state_owner")]
    pub otel_sampling_state_owner: Option<Py<Context>>,
}

impl Default for Context {
    fn default() -> Self {
        Python::attach(|py| Context {
            trace_id: None,
            span_id: None,
            meta: PyDict::new(py).unbind(),
            metrics: PyDict::new(py).unbind(),
            baggage: None,
            span_links: None,
            is_remote: false,
            reactivate: false,
            otel_sampling_state_data: None,
            otel_sampling_state_owner: None,
        })
    }
}

impl Context {
    fn sampling_priority_f64(&mut self, py: Python<'_>) -> PyResult<Option<f64>> {
        let metrics = self.get_metrics(py);
        match metrics.get_item(SAMPLING_PRIORITY_KEY)? {
            Some(v) => Ok(Some(v.extract::<f64>()?)),
            None => Ok(None),
        }
    }

    fn trace_flags(&mut self, py: Python<'_>) -> PyResult<u8> {
        let meta = self.get_meta(py);
        let inherited_flags = meta
            .get_item(W3C_TRACEPARENT_KEY)?
            .and_then(|value| value.extract::<String>().ok())
            .and_then(|value| {
                value
                    .get(53..55)
                    .and_then(|flags| u8::from_str_radix(flags, 16).ok())
            })
            .unwrap_or(0)
            & 0x2;
        let sampled = self
            .sampling_priority_f64(py)?
            .map(|priority| priority > 0.0)
            .unwrap_or(false) as u8;
        Ok(inherited_flags | sampled)
    }

    fn effective_otel_sampling_state(slf: &Bound<'_, Self>) -> Option<f64> {
        let py = slf.py();
        let owner = slf
            .borrow()
            .otel_sampling_state_owner
            .as_ref()
            .map(|owner| owner.clone_ref(py));
        match owner {
            Some(owner) => owner.bind(py).borrow().otel_sampling_state_data,
            None => slf.borrow().otel_sampling_state_data,
        }
    }

    fn set_otel_sampling_state(slf: &Bound<'_, Self>, value: Option<f64>) {
        let py = slf.py();
        let owner = slf
            .borrow()
            .otel_sampling_state_owner
            .as_ref()
            .map(|owner| owner.clone_ref(py));
        match owner {
            Some(owner) => owner.bind(py).borrow_mut().otel_sampling_state_data = value,
            None => slf.borrow_mut().otel_sampling_state_data = value,
        }
    }

    fn materialize_otel_sampling_decision(slf: &Bound<'_, Self>) -> PyResult<()> {
        let Some(sample_rate) = Self::effective_otel_sampling_state(slf) else {
            return Ok(());
        };

        let py = slf.py();
        let (trace_id, sampled, meta) = {
            let mut this = slf.borrow_mut();
            let sampled = this
                .sampling_priority_f64(py)?
                .map(|priority| priority > 0.0)
                .unwrap_or(false);
            (this.trace_id, sampled, this.get_meta(py))
        };
        let probabilistic_decision = sample_rate >= 0.0;
        materialize_otel_sampling_decision_fn(py)?.call1((
            meta,
            trace_id,
            sampled,
            if probabilistic_decision {
                sample_rate
            } else {
                0.0
            },
            probabilistic_decision,
        ))?;
        Self::set_otel_sampling_state(slf, None);
        Ok(())
    }

    fn build_tracestate(slf: &Bound<'_, Self>, parent_id: Option<u128>) -> PyResult<Vec<String>> {
        Self::materialize_otel_sampling_decision(slf)?;
        let py = slf.py();
        let mut dd_list_member: String = w3c_get_dd_list_member_fn(py)?.call1((slf,))?.extract()?;
        if let Some(parent_id) = parent_id {
            let parent_member = format!("p:{:016x}", parent_id);
            dd_list_member = if dd_list_member.is_empty() {
                parent_member
            } else {
                format!("{};{}", parent_member, dd_list_member)
            };
        }

        let raw_tracestate = {
            let mut this = slf.borrow_mut();
            this.get_meta(py)
                .get_item(W3C_TRACESTATE_KEY)?
                .map(|value| value.extract::<String>())
                .transpose()?
                .unwrap_or_default()
        };
        if raw_tracestate.is_empty() {
            return Ok(if dd_list_member.is_empty() {
                Vec::new()
            } else {
                vec![format!("dd={}", dd_list_member)]
            });
        }
        if raw_tracestate.starts_with("ot=")
            && !raw_tracestate.contains(',')
            && raw_tracestate.is_ascii()
        {
            if dd_list_member.is_empty() {
                return Ok(vec![raw_tracestate]);
            }
            let dd_member = format!("dd={}", dd_list_member);
            if dd_member.len() + raw_tracestate.len() < dd_trace_tracestate_max_bytes(py)? {
                return Ok(vec![dd_member, raw_tracestate]);
            }
            return Ok(vec![dd_member]);
        }
        w3c_build_tracestate_members_fn(py)?
            .call1((raw_tracestate, dd_list_member))?
            .extract()
    }
}

#[pyo3::pymethods]
impl Context {
    #[new]
    #[classmethod]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        trace_id=None,
        span_id=None,
        dd_origin=None,
        sampling_priority=None,
        meta=None,
        metrics=None,
        span_links=None,
        baggage=None,
        is_remote=true,
    ))]
    pub fn __new__<'p>(
        _: &Bound<'p, pyo3::types::PyType>,
        py: Python<'p>,
        trace_id: Option<&Bound<'p, PyAny>>,
        span_id: Option<&Bound<'p, PyAny>>,
        dd_origin: Option<&Bound<'p, PyAny>>,
        sampling_priority: Option<&Bound<'p, PyAny>>,
        meta: Option<&Bound<'p, PyDict>>,
        metrics: Option<&Bound<'p, PyDict>>,
        span_links: Option<&Bound<'p, PyList>>,
        baggage: Option<&Bound<'p, PyDict>>,
        is_remote: bool,
    ) -> PyResult<Self> {
        let meta_dict = meta
            .map(|d| d.clone().unbind())
            .unwrap_or_else(|| PyDict::new(py).unbind());
        if let Some(origin) = dd_origin {
            if let Ok(s) = origin.extract::<&str>() {
                if is_printable_ascii(s) {
                    meta_dict.bind(py).set_item(ORIGIN_KEY, s)?;
                }
            }
        }
        let metrics_dict = metrics
            .map(|d| d.clone().unbind())
            .unwrap_or_else(|| PyDict::new(py).unbind());
        if let Some(sp) = sampling_priority {
            metrics_dict.bind(py).set_item(SAMPLING_PRIORITY_KEY, sp)?;
        }
        if meta_dict.bind(py).contains(W3C_TRACESTATE_KEY)? {
            normalize_otel_tracestate_fn(py)?.call1((meta_dict.bind(py),))?;
        }
        Ok(Self {
            trace_id: extract_trace_id(trace_id),
            span_id: extract_span_id(span_id),
            meta: meta_dict,
            metrics: metrics_dict,
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
            is_remote,
            reactivate: false,
            otel_sampling_state_data: None,
            otel_sampling_state_owner: None,
        })
    }

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
            Some(_) => {
                if let Some(id) = extract_trace_id(value) {
                    self.trace_id = Some(id);
                }
            }
        }
    }

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
            Some(_) => {
                if let Some(id) = extract_span_id(value) {
                    self.span_id = Some(id);
                }
            }
        }
    }

    #[getter(_meta)]
    #[inline(always)]
    fn get_meta<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.meta.bind(py).clone()
    }

    #[setter(_meta)]
    #[inline(always)]
    fn set_meta(&mut self, value: &Bound<'_, PyDict>) {
        self.meta = value.clone().unbind();
    }

    #[getter(_metrics)]
    #[inline(always)]
    fn get_metrics<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.metrics.bind(py).clone()
    }

    #[setter(_metrics)]
    #[inline(always)]
    fn set_metrics(&mut self, value: &Bound<'_, PyDict>) {
        self.metrics = value.clone().unbind();
    }

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

    #[getter]
    fn get_sampling_priority<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let metrics = self.get_metrics(py);
        metrics.get_item(SAMPLING_PRIORITY_KEY)
    }

    #[setter]
    fn set_sampling_priority(
        &mut self,
        py: Python<'_>,
        value: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<()> {
        let metrics = self.get_metrics(py);
        match value {
            None => del_item_if_present(&metrics, SAMPLING_PRIORITY_KEY)?,
            Some(v) => metrics.set_item(SAMPLING_PRIORITY_KEY, v)?,
        }
        Ok(())
    }

    #[getter]
    fn get_dd_origin<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyAny>>> {
        let meta = self.get_meta(py);
        meta.get_item(ORIGIN_KEY)
    }

    #[setter]
    fn set_dd_origin(&mut self, py: Python<'_>, value: Option<&Bound<'_, PyAny>>) -> PyResult<()> {
        let meta = self.get_meta(py);
        match value {
            None => del_item_if_present(&meta, ORIGIN_KEY)?,
            Some(v) => meta.set_item(ORIGIN_KEY, v)?,
        }
        Ok(())
    }

    #[getter]
    fn get_dd_user_id(&mut self, py: Python<'_>) -> PyResult<Option<String>> {
        let meta = self.get_meta(py);
        let Some(item) = meta.get_item(USER_ID_KEY)? else {
            return Ok(None);
        };
        let encoded: String = item.extract()?;
        if encoded.is_empty() {
            return Ok(None);
        }
        let decoded = BASE64
            .decode(encoded.as_bytes())
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let s = String::from_utf8(decoded).map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(Some(s))
    }

    #[setter]
    fn set_dd_user_id(&mut self, py: Python<'_>, value: Option<&str>) -> PyResult<()> {
        let meta = self.get_meta(py);
        match value {
            None => del_item_if_present(&meta, USER_ID_KEY)?,
            Some(v) => meta.set_item(USER_ID_KEY, BASE64.encode(v.as_bytes()))?,
        }
        Ok(())
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__trace_id_64bits(&self) -> Option<u128> {
        self.trace_id.map(|t| t & MAX_UINT_64BITS)
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__trace_flags(&mut self, py: Python<'_>) -> PyResult<u8> {
        self.trace_flags(py)
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__traceflags(&mut self, py: Python<'_>) -> PyResult<String> {
        Ok(format!("{:02x}", self.trace_flags(py)?))
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__traceparent(&mut self, py: Python<'_>) -> PyResult<String> {
        let meta = self.get_meta(py);
        let tp: Option<String> = meta
            .get_item(W3C_TRACEPARENT_KEY)?
            .map(|v| v.extract::<String>())
            .transpose()?;
        let (Some(trace_id), Some(span_id)) = (self.trace_id, self.span_id) else {
            return Ok(tp.unwrap_or_default());
        };
        let trace_id_hex = match tp.as_deref() {
            Some(tp) if !tp.is_empty() => tp.split('-').nth(1).unwrap_or_default().to_string(),
            _ => format!("{:032x}", trace_id),
        };
        Ok(format!(
            "00-{}-{:016x}-{:02x}",
            trace_id_hex,
            span_id,
            self.trace_flags(py)?
        ))
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__tracestate(slf: &Bound<'_, Self>) -> PyResult<String> {
        Ok(Self::build_tracestate(slf, None)?.join(","))
    }

    #[pyo3(signature = (parent_id=None))]
    fn _tracestate_entries(
        slf: &Bound<'_, Self>,
        parent_id: Option<u128>,
    ) -> PyResult<Vec<(String, String)>> {
        Ok(Self::build_tracestate(slf, parent_id)?
            .into_iter()
            .map(|member| {
                let (key, value) = member.split_once('=').unwrap_or((&member, ""));
                (key.to_string(), value.to_string())
            })
            .collect())
    }

    fn _publish_sampling_decision(
        slf: &Bound<'_, Self>,
        sampling_priority: Option<&Bound<'_, PyAny>>,
        sample_rate: f64,
        probabilistic_decision: bool,
    ) -> PyResult<()> {
        Self::set_otel_sampling_state(
            slf,
            Some(if probabilistic_decision && sample_rate > 0.0 {
                sample_rate
            } else {
                -1.0
            }),
        );
        let py = slf.py();
        let metrics = slf.borrow_mut().get_metrics(py);
        match sampling_priority {
            Some(priority) => metrics.set_item(SAMPLING_PRIORITY_KEY, priority)?,
            None => del_item_if_present(&metrics, SAMPLING_PRIORITY_KEY)?,
        }
        Ok(())
    }

    fn set_baggage_item(
        &mut self,
        py: Python<'_>,
        key: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        self.get_baggage(py).set_item(key, value)
    }

    fn get_baggage_item<'py>(
        &mut self,
        py: Python<'py>,
        key: &str,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        self.get_baggage(py).get_item(key)
    }

    fn get_all_baggage_items<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.get_baggage(py)
    }

    fn remove_baggage_item(&mut self, py: Python<'_>, key: &str) -> PyResult<()> {
        del_item_if_present(&self.get_baggage(py), key)
    }

    fn remove_all_baggage_items(&mut self, py: Python<'_>) -> PyResult<()> {
        self.get_baggage(py).clear();
        Ok(())
    }

    /// PERF: run once per child span. Constructs a plain `Context` directly via
    /// the Rust struct, bypassing any subclass `__new__`/`__init__`, and reuses
    /// the shared `_meta`/`_metrics`/`_baggage` references, trusting that this
    /// data has already been validated. This mirrors the previous Python
    /// implementation's `Context.__new__(Context)` behavior.
    #[pyo3(signature = (trace_id, span_id))]
    fn copy<'py>(
        slf: &Bound<'py, Self>,
        trace_id: &Bound<'py, PyAny>,
        span_id: &Bound<'py, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let py = slf.py();
        let (meta, metrics, baggage, otel_sampling_state_owner);
        {
            let mut this = slf.borrow_mut();
            meta = this.get_meta(py);
            metrics = this.get_metrics(py);
            baggage = this.get_baggage(py);
            otel_sampling_state_owner = this
                .otel_sampling_state_owner
                .as_ref()
                .map(|owner| owner.clone_ref(py))
                .or_else(|| Some(slf.clone().unbind()));
        }
        let new_ctx = Self {
            trace_id: extract_trace_id(Some(trace_id)),
            span_id: extract_span_id(Some(span_id)),
            meta: meta.unbind(),
            metrics: metrics.unbind(),
            baggage: Some(baggage.unbind()),
            span_links: Some(PyList::empty(py).unbind()),
            is_remote: false,
            reactivate: false,
            otel_sampling_state_data: None,
            otel_sampling_state_owner,
        };
        Ok(Py::new(py, new_ctx)?.into_any())
    }

    fn _with_baggage_item<'py>(
        slf: &Bound<'py, Self>,
        key: &Bound<'py, PyAny>,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let py = slf.py();
        let (trace_id, span_id, meta, metrics, new_baggage, otel_sampling_state_owner);
        {
            let mut this = slf.borrow_mut();
            let baggage = this.get_baggage(py);
            new_baggage = baggage.copy()?;
            new_baggage.set_item(key, value)?;
            meta = this.get_meta(py);
            metrics = this.get_metrics(py);
            trace_id = this.trace_id;
            span_id = this.span_id;
            otel_sampling_state_owner = this
                .otel_sampling_state_owner
                .as_ref()
                .map(|owner| owner.clone_ref(py))
                .unwrap_or_else(|| slf.clone().unbind());
        }
        let cls = slf.get_type();
        let kwargs = PyDict::new(py);
        kwargs.set_item("trace_id", trace_id)?;
        kwargs.set_item("span_id", span_id)?;
        let new_ctx = cls.call((), Some(&kwargs))?;
        new_ctx.setattr("_meta", meta)?;
        new_ctx.setattr("_metrics", metrics)?;
        new_ctx.setattr("_baggage", new_baggage)?;
        new_ctx.setattr("_otel_sampling_state_owner", otel_sampling_state_owner)?;
        Ok(new_ctx.unbind())
    }

    fn __enter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, Self>> {
        Ok(slf.clone())
    }

    #[pyo3(signature = (*_args))]
    fn __exit__(_slf: &Bound<'_, Self>, _args: &Bound<'_, PyTuple>) -> PyResult<()> {
        Ok(())
    }

    // NOTE: span_id/_reactivate are deliberately excluded. A Context compares equal
    // to any per-span copy() of itself (differing only in span_id) -- e.g.
    // Span.context builds one such copy per child span -- so equality here means
    // "same trace-level state", not "same span".
    fn __eq__(slf: &Bound<'_, Self>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        let py = slf.py();
        if !other.is_instance_of::<Self>() {
            return Ok(false);
        }
        let other = other.cast::<Self>()?;

        if slf.borrow().trace_id != other.borrow().trace_id {
            return Ok(false);
        }
        let self_meta = slf.borrow_mut().get_meta(py);
        let other_meta = other.borrow_mut().get_meta(py);
        if !self_meta.eq(other_meta)? {
            return Ok(false);
        }
        let self_metrics = slf.borrow_mut().get_metrics(py);
        let other_metrics = other.borrow_mut().get_metrics(py);
        if !self_metrics.eq(other_metrics)? {
            return Ok(false);
        }
        let self_span_links = slf.borrow_mut().get_span_links(py);
        let other_span_links = other.borrow_mut().get_span_links(py);
        if !self_span_links.eq(other_span_links)? {
            return Ok(false);
        }
        let self_baggage = slf.borrow_mut().get_baggage(py);
        let other_baggage = other.borrow_mut().get_baggage(py);
        if !self_baggage.eq(other_baggage)? {
            return Ok(false);
        }
        Ok(slf.borrow().is_remote == other.borrow().is_remote
            && Self::effective_otel_sampling_state(slf)
                == Self::effective_otel_sampling_state(other))
    }

    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {
        let py = slf.py();
        let (trace_id, span_id, is_remote) = {
            let this = slf.borrow();
            (this.trace_id, this.span_id, this.is_remote)
        };
        let meta = slf.borrow_mut().get_meta(py);
        let metrics = slf.borrow_mut().get_metrics(py);
        let span_links = slf.borrow_mut().get_span_links(py);
        let baggage = slf.borrow_mut().get_baggage(py);
        Ok(format!(
            "Context(trace_id={}, span_id={}, _meta={}, _metrics={}, _span_links={}, _baggage={}, _is_remote={})",
            trace_id.map_or_else(|| "None".to_string(), |v| v.to_string()),
            span_id.map_or_else(|| "None".to_string(), |v| v.to_string()),
            meta.repr()?,
            metrics.repr()?,
            span_links.repr()?,
            baggage.repr()?,
            if is_remote { "True" } else { "False" },
        ))
    }

    fn __hash__(&self, py: Python<'_>) -> PyResult<isize> {
        match self.trace_id {
            Some(t) => t.into_pyobject(py).expect("u128 into_pyobject").hash(),
            None => py.None().bind(py).hash(),
        }
    }

    fn __getstate__<'py>(slf: &Bound<'py, Self>) -> ContextState<'py> {
        let py = slf.py();
        let otel_sampling_state = Self::effective_otel_sampling_state(slf);
        let mut this = slf.borrow_mut();
        (
            this.trace_id,
            this.span_id,
            this.get_meta(py),
            this.get_metrics(py),
            this.get_span_links(py),
            this.get_baggage(py),
            this.is_remote,
            this.reactivate,
            otel_sampling_state,
        )
    }

    fn __setstate__(&mut self, _py: Python<'_>, state: &Bound<'_, PyTuple>) -> PyResult<()> {
        self.trace_id = state.get_item(0)?.extract()?;
        self.span_id = state.get_item(1)?.extract()?;
        self.meta = state.get_item(2)?.extract::<Bound<'_, PyDict>>()?.unbind();
        self.metrics = state.get_item(3)?.extract::<Bound<'_, PyDict>>()?.unbind();
        self.span_links = Some(state.get_item(4)?.extract::<Bound<'_, PyList>>()?.unbind());
        self.baggage = Some(state.get_item(5)?.extract::<Bound<'_, PyDict>>()?.unbind());
        self.is_remote = state.get_item(6)?.extract()?;
        self.reactivate = state.get_item(7)?.extract()?;
        self.otel_sampling_state_data = if state.len()? > 8 {
            state.get_item(8)?.extract()?
        } else {
            None
        };
        self.otel_sampling_state_owner = None;
        Ok(())
    }

    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        visit.call(&self.meta)?;
        visit.call(&self.metrics)?;
        if let Some(d) = &self.baggage {
            visit.call(d)?;
        }
        if let Some(l) = &self.span_links {
            visit.call(l)?;
        }
        if let Some(owner) = &self.otel_sampling_state_owner {
            visit.call(owner)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        *self = Self::default();
    }
}
