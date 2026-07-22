use libdd_capabilities_impl::NativeCapabilities;
use libdd_data_pipeline::trace_exporter::{
    agent_response::AgentResponse, TelemetryConfig, TraceExporter, TraceExporterBuilder,
    TraceExporterInputFormat, TraceExporterOutputFormat,
};
use libdd_shared_runtime::ForkSafeRuntime;
use libdd_trace_utils::span::v04::Span;
use pyo3::types::PyString;
use pyo3::{
    exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes, PyTraverseError, PyVisit,
};
use std::sync::Mutex;
use std::time::Duration;
mod agent_response;
mod exceptions;
use crate::py_string::{PyBackedString, PyTraceData};
use crate::shared_runtime::SharedRuntimePy;
use crate::span::{get_packb, traverse_v04_span, SpanData};
use exceptions::TraceExporterErrorPy;

/// A wrapper around [TraceExporterBuilder]
///
/// Allows to use the builder as a python class. Only one exporter can be built using a builder
/// once `build` has been called the builder shouldn't be reused.
#[pyclass(name = "TraceExporterBuilder")]
pub struct TraceExporterBuilderPy {
    builder: Option<TraceExporterBuilder<ForkSafeRuntime>>,
}

impl TraceExporterBuilderPy {
    fn try_as_mut(&mut self) -> PyResult<&mut TraceExporterBuilder<ForkSafeRuntime>> {
        self.builder
            .as_mut()
            .ok_or(PyValueError::new_err("Builder has already been consumed"))
    }
}

#[pymethods]
impl TraceExporterBuilderPy {
    #[new]
    fn new() -> Self {
        TraceExporterBuilderPy {
            builder: Some(TraceExporterBuilder::default()),
        }
    }

    fn set_hostname(mut slf: PyRefMut<'_, Self>, hostname: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_hostname(hostname);
        Ok(slf.into())
    }

    fn set_url(mut slf: PyRefMut<'_, Self>, url: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_url(url);
        Ok(slf.into())
    }

    fn set_dogstatsd_url(mut slf: PyRefMut<'_, Self>, url: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_dogstatsd_url(url);
        Ok(slf.into())
    }

    fn set_env(mut slf: PyRefMut<'_, Self>, env: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_env(env);
        Ok(slf.into())
    }

    fn set_app_version(mut slf: PyRefMut<'_, Self>, version: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_app_version(version);
        Ok(slf.into())
    }

    fn set_service(mut slf: PyRefMut<'_, Self>, service: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_service(service);
        Ok(slf.into())
    }

    fn set_git_commit_sha(
        mut slf: PyRefMut<'_, Self>,
        git_commit_sha: &'_ str,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_git_commit_sha(git_commit_sha);
        Ok(slf.into())
    }

    fn set_process_tags(mut slf: PyRefMut<'_, Self>, process_tags: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_process_tags(process_tags);
        Ok(slf.into())
    }

    fn set_tracer_version(mut slf: PyRefMut<'_, Self>, version: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_tracer_version(version);
        Ok(slf.into())
    }

    fn set_language(mut slf: PyRefMut<'_, Self>, language: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language(language);
        Ok(slf.into())
    }

    fn set_language_version(mut slf: PyRefMut<'_, Self>, version: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language_version(version);
        Ok(slf.into())
    }

    fn set_language_interpreter(
        mut slf: PyRefMut<'_, Self>,
        interpreter: &'_ str,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language_interpreter(interpreter);
        Ok(slf.into())
    }

    fn set_language_interpreter_vendor(
        mut slf: PyRefMut<'_, Self>,
        vendor: &'_ str,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_language_interpreter_vendor(vendor);
        Ok(slf.into())
    }

    fn set_test_session_token(mut slf: PyRefMut<'_, Self>, token: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_test_session_token(token);
        Ok(slf.into())
    }

    fn set_input_format(mut slf: PyRefMut<'_, Self>, input_format: &str) -> PyResult<Py<Self>> {
        let input_format = match input_format {
            "v0.4" => Ok(TraceExporterInputFormat::V04),
            "v0.5" => Ok(TraceExporterInputFormat::V05),
            _ => Err(PyValueError::new_err("Invalid trace format")),
        }?;
        slf.try_as_mut()?.set_input_format(input_format);
        Ok(slf.into())
    }

    fn set_output_format(mut slf: PyRefMut<'_, Self>, output_format: &str) -> PyResult<Py<Self>> {
        let output_format = match output_format {
            "v0.4" => Ok(TraceExporterOutputFormat::V04),
            "v0.5" => Ok(TraceExporterOutputFormat::V05),
            _ => Err(PyValueError::new_err("Invalid trace format")),
        }?;
        slf.try_as_mut()?.set_output_format(output_format);
        Ok(slf.into())
    }

    fn set_client_computed_top_level(mut slf: PyRefMut<'_, Self>) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_client_computed_top_level();
        Ok(slf.into())
    }

    fn set_client_computed_stats(mut slf: PyRefMut<'_, Self>) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_client_computed_stats();
        Ok(slf.into())
    }

    fn enable_stats(mut slf: PyRefMut<'_, Self>, bucket_size_ns: u64) -> PyResult<Py<Self>> {
        slf.try_as_mut()?
            .enable_stats(Duration::from_nanos(bucket_size_ns));
        Ok(slf.into())
    }

    fn enable_client_side_stats_obfuscation(mut slf: PyRefMut<'_, Self>) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.enable_client_side_stats_obfuscation();
        Ok(slf.into())
    }

    fn enable_telemetry(
        mut slf: PyRefMut<'_, Self>,
        heartbeat_ms: u64,
        runtime_id: String,
        debug_enabled: bool,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.enable_telemetry(TelemetryConfig {
            heartbeat: heartbeat_ms,
            runtime_id: Some(runtime_id),
            debug_enabled,
        });
        Ok(slf.into())
    }

    fn enable_health_metrics(mut slf: PyRefMut<'_, Self>) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.enable_health_metrics();
        Ok(slf.into())
    }

    fn set_otlp_endpoint(mut slf: PyRefMut<'_, Self>, url: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_otlp_endpoint(url);
        Ok(slf.into())
    }

    fn set_otlp_headers(
        mut slf: PyRefMut<'_, Self>,
        headers: Vec<(String, String)>,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_otlp_headers(headers);
        Ok(slf.into())
    }

    fn set_otlp_metrics_endpoint(mut slf: PyRefMut<'_, Self>, url: &'_ str) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_otlp_metrics_endpoint(url);
        Ok(slf.into())
    }

    fn set_otlp_metrics_headers(
        mut slf: PyRefMut<'_, Self>,
        headers: Vec<(String, String)>,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_otlp_metrics_headers(headers);
        Ok(slf.into())
    }

    fn enable_otel_trace_semantics(mut slf: PyRefMut<'_, Self>) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.enable_otel_trace_semantics();
        Ok(slf.into())
    }

    fn set_connection_timeout(mut slf: PyRefMut<'_, Self>, timeout_ms: u64) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.set_connection_timeout(Some(timeout_ms));
        Ok(slf.into())
    }

    /// Consumes the wrapped builder, requires a shared runtime to be passed to spawn async tasks.
    ///
    /// The builder shouldn't be reused.
    ///
    /// `set_shared_runtime` must be specified on the worker to avoid the trace exporter creating
    /// one without registering the fork hooks.
    /// `encode_links_as_json`/`encode_events_as_json` are fixed for the exporter's output
    /// format (true for v0.5, which has no wire fields for span links/events); they are applied
    /// to every span at `put_trace` time. `encode_events_as_json` is additionally true on v0.4
    /// when the agent hasn't opted into native span events; span links have no such gate.
    fn build(
        &mut self,
        shared_runtime: PyRef<'_, SharedRuntimePy>,
        encode_links_as_json: bool,
        encode_events_as_json: bool,
    ) -> PyResult<TraceExporterPy> {
        let shared_runtime = shared_runtime.as_arc().clone();
        self.try_as_mut()?.set_shared_runtime(shared_runtime);
        let exporter = TraceExporterPy {
            inner: Some(
                self.builder
                    .take()
                    .ok_or(PyValueError::new_err("Builder has already been consumed"))?
                    .build::<NativeCapabilities>()
                    .map_err(|err| PyValueError::new_err(format!("Builder {err}")))?,
            ),
            buffer: Mutex::new(TraceBuffer { chunks: Vec::new() }),
            encode_links_as_json,
            encode_events_as_json,
        };
        Ok(exporter)
    }

    fn debug(&self) -> String {
        format!("{:?}", self.builder)
    }
}

/// Native buffer of fully-built libdatadog v0.4 trace chunks awaiting flush. Spans are converted
/// to the wire format at `put_trace` time (on the request thread), so `flush` is a pure
/// `mem::take` + off-GIL network send with no per-span work — see `put_trace`'s doc comment.
///
/// No size or count limits are enforced here; bounding the buffered data is libdatadog's
/// responsibility.
struct TraceBuffer {
    chunks: Vec<Vec<Span<PyTraceData>>>,
}

/// Outcome of [`TraceExporterPy::put_trace`]: the trace was buffered, or it had no encodable
/// spans. Size/count-based drops were removed — buffering limits belong to libdatadog now.
#[pyclass(name = "PutOutcome", eq, eq_int, skip_from_py_object)]
#[derive(PartialEq, Clone, Copy, Debug)]
pub enum PutOutcome {
    Accepted,
    NoEncodableSpans,
}

/// A python object wrapping a [TraceExporter] instance plus a native span buffer.
#[pyclass(name = "TraceExporter")]
pub struct TraceExporterPy {
    inner: Option<TraceExporter<NativeCapabilities, ForkSafeRuntime>>,
    buffer: Mutex<TraceBuffer>,
    /// Fixed for the exporter's output format; applied to every span at `put_trace`.
    encode_links_as_json: bool,
    /// Fixed for the exporter's output format / native-span-events opt-in; applied to every
    /// span at `put_trace`.
    encode_events_as_json: bool,
}

impl TraceExporterPy {
    /// Lock the native span buffer, recovering from poisoning instead of panicking — a
    /// poisoned lock would otherwise permanently break every subsequent put_trace/flush call.
    fn lock_buffer(&self) -> std::sync::MutexGuard<'_, TraceBuffer> {
        self.buffer
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

#[pymethods]
impl TraceExporterPy {
    /// Send a msgpack encoded trace payload.
    ///
    /// The payload is passed as an immutable `bytes` object to be able to release the GIL while
    /// sending the traces.
    fn send(&self, py: Python<'_>, data: PyBackedBytes) -> PyResult<String> {
        py.detach(move || {
            match self
                .inner
                .as_ref()
                .ok_or(PyValueError::new_err(
                    "TraceExporter has already been consumed",
                ))?
                .send(&data)
            {
                Ok(res) => match res {
                    AgentResponse::Changed { body } => Ok(body),
                    AgentResponse::Unchanged => Ok("".to_string()),
                },
                Err(e) => Err(TraceExporterErrorPy::from(e).into()),
            }
        })
    }

    /// Build one trace chunk (a Python ``list[Span]``) into libdatadog v0.4 wire spans and buffer
    /// it for the next flush.
    ///
    /// The full wire-format conversion (`SpanData::build_v04_span`: `clone_ref`, string
    /// truncation, `VecMap` construction, the `meta_struct` `packb` and v0.5 links/events
    /// `json.dumps` Python calls) runs right here, under the GIL, bounded by the size of *this*
    /// trace chunk. This spreads the cost across every request instead of paying it as one
    /// periodic burst at flush — the same tail-latency property the old Cython encoder relied on
    /// — and lets `flush` be a pure `mem::take` + off-GIL send. Returns an outcome rather than
    /// raising.
    #[pyo3(signature = (spans, dd_origin=None))]
    fn put_trace(
        &self,
        py: Python<'_>,
        spans: Vec<Py<SpanData>>,
        dd_origin: Option<Py<PyString>>,
    ) -> PyResult<PutOutcome> {
        if spans.is_empty() {
            return Ok(PutOutcome::NoEncodableSpans);
        }

        let has_packb = get_packb(py).is_some();
        let encode_links_as_json = self.encode_links_as_json;
        let encode_events_as_json = self.encode_events_as_json;
        let origin: Option<PyBackedString> = match &dd_origin {
            Some(o) => PyBackedString::try_from(o.bind(py).clone()).ok(),
            None => None,
        };

        let mut chunk = Vec::with_capacity(spans.len());
        for span in &spans {
            let span_ref = span.bind(py).borrow();
            chunk.push(span_ref.build_v04_span(
                py,
                origin.as_ref(),
                encode_links_as_json,
                encode_events_as_json,
                has_packb,
            )?);
            // Borrow released at end of iteration, before the next span is built.
        }

        self.lock_buffer().chunks.push(chunk);
        Ok(PutOutcome::Accepted)
    }

    /// Send the buffered, already-built v0.4 trace chunks directly to the agent via
    /// [`TraceExporter::send_trace_chunks`], bypassing msgpack encode/decode.
    ///
    /// `flush` does no per-span work: it `mem::take`s the buffer (dropping the lock before the
    /// network call) and hands the chunks straight to the exporter. Every `Span<PyTraceData>`
    /// field is GIL-free-readable, so the send runs fully detached and never blocks other Python
    /// threads.
    ///
    /// Returns ``(n_traces_sent, response_body)`` where ``response_body`` is the agent's JSON
    /// body for a changed sampling rate, else ``None``.
    fn flush(&self, py: Python<'_>) -> PyResult<(usize, Option<String>)> {
        let chunks = {
            let mut buf = self.lock_buffer();
            std::mem::take(&mut buf.chunks)
        };
        if chunks.is_empty() {
            return Ok((0, None));
        }

        let n = chunks.len();
        let res: PyResult<AgentResponse> = py.detach(move || {
            let exporter = self
                .inner
                .as_ref()
                .ok_or_else(|| PyValueError::new_err("TraceExporter has already been consumed"))?;
            exporter
                .send_trace_chunks(chunks, None)
                .map_err(|e| TraceExporterErrorPy::from(e).into())
        });
        match res? {
            AgentResponse::Changed { body } => Ok((n, Some(body))),
            AgentResponse::Unchanged => Ok((n, None)),
        }
    }

    /// Cyclic-GC traversal: the buffer holds built v0.4 spans whose `PyBackedString`/`Bytes`
    /// fields own live Python objects (see `traverse_v04_span`) that can close reference cycles
    /// (span → context → tracer → writer → this exporter). Best-effort: if the buffer is
    /// momentarily locked, skip this cycle rather than risk blocking GC.
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        let guard = match self.buffer.try_lock() {
            Ok(g) => g,
            Err(std::sync::TryLockError::Poisoned(p)) => p.into_inner(),
            Err(std::sync::TryLockError::WouldBlock) => return Ok(()),
        };
        for chunk in &guard.chunks {
            for span in chunk {
                traverse_v04_span(span, &visit)?;
            }
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        if let Ok(buf) = self.buffer.get_mut() {
            buf.chunks.clear();
        }
    }

    /// Number of trace chunks currently buffered (replaces ``len(encoder)`` for drop-rate).
    fn buffered_traces(&self) -> usize {
        self.lock_buffer().chunks.len()
    }

    fn shutdown(&mut self, timeout_ns: u64) -> PyResult<()> {
        if let Some(exporter) = self.inner.take() {
            exporter
                .shutdown(Some(Duration::from_nanos(timeout_ns)))
                .map_err(TraceExporterErrorPy::from)?;
        }
        Ok(())
    }

    fn drop(&mut self) -> PyResult<()> {
        drop(self.inner.take());
        Ok(())
    }

    fn debug(&self) -> String {
        format!("{:?}", self.inner)
    }
}

impl Drop for TraceExporterPy {
    fn drop(&mut self) {
        if let Some(exporter) = self.inner.take() {
            let _ = exporter.shutdown(Some(Duration::from_secs(3)));
        }
    }
}

#[pymodule]
pub fn register_data_pipeline(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TraceExporterBuilderPy>()?;
    m.add_class::<TraceExporterPy>()?;
    m.add_class::<PutOutcome>()?;
    exceptions::register_exceptions(m)?;
    agent_response::register_agent_response(m)?;

    Ok(())
}
