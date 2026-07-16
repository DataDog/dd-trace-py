use libdd_capabilities_impl::NativeCapabilities;
use libdd_data_pipeline::trace_exporter::{
    agent_response::AgentResponse, TelemetryConfig, TraceExporter, TraceExporterBuilder,
    TraceExporterInputFormat, TraceExporterOutputFormat,
};
use libdd_shared_runtime::ForkSafeRuntime;
use libdd_trace_utils::span::v04::Span;
use pyo3::types::{PyBytes, PyString};
use pyo3::{
    exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes, PyTraverseError, PyVisit,
};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
mod agent_response;
mod exceptions;
use crate::get_or_init;
use crate::py_string::{Bytes, PyBackedString, PyTraceData};
use crate::shared_runtime::SharedRuntimePy;
use crate::span::SpanData;
use exceptions::TraceExporterErrorPy;

// `ddtrace.internal._encoding.packb`, cached to avoid a module lookup on every put_trace call.
static PACKB: OnceLock<Py<PyAny>> = OnceLock::new();

fn get_packb(py: Python<'_>) -> Option<&'static Py<PyAny>> {
    get_or_init!(PACKB, py, {
        py.import("ddtrace.internal._encoding")
            .and_then(|m| m.getattr("packb"))
            .map(|a| a.unbind())
    })
    .ok()
}

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
    /// `encode_links_events_as_json` is fixed for the exporter's output format (true for v0.5,
    /// which has no wire fields for span links/events); it is applied to every span at flush.
    fn build(
        &mut self,
        shared_runtime: PyRef<'_, SharedRuntimePy>,
        encode_links_events_as_json: bool,
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
            encode_links_events_as_json,
        };
        Ok(exporter)
    }

    fn debug(&self) -> String {
        format!("{:?}", self.builder)
    }
}

/// One buffered trace: the raw Python span objects plus the trace-level origin, kept
/// *unconverted*. `build_v04_span` is deferred to `flush` (the background thread) so the
/// per-request path only incref-and-stashes.
struct BufferedChunk {
    spans: Vec<Py<SpanData>>,
    dd_origin: Option<Py<PyString>>,
}

/// Native buffer of unconverted trace chunks awaiting flush.
///
/// No size or count limits are enforced here; bounding the buffered data is libdatadog's
/// responsibility.
struct TraceBuffer {
    chunks: Vec<BufferedChunk>,
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
    /// Fixed for the exporter's output format; applied to every span at flush.
    encode_links_events_as_json: bool,
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

    /// Buffer one trace chunk (a Python ``list[Span]``) for the next flush.
    ///
    /// This only incref-and-stashes the raw span objects and the trace-level ``dd_origin``;
    /// the conversion to libdatadog v0.4 spans (`build_v04_span`) is deferred to `flush`, which
    /// runs on the background writer thread. Returns an outcome rather than raising.
    #[pyo3(signature = (spans, dd_origin=None))]
    fn put_trace(
        &self,
        spans: Vec<Py<SpanData>>,
        dd_origin: Option<Py<PyString>>,
    ) -> PyResult<PutOutcome> {
        if spans.is_empty() {
            return Ok(PutOutcome::NoEncodableSpans);
        }
        self.lock_buffer()
            .chunks
            .push(BufferedChunk { spans, dd_origin });
        Ok(PutOutcome::Accepted)
    }

    /// Convert the buffered spans to libdatadog v0.4 spans (`build_v04_span`, GIL held) and send
    /// them directly to the agent via [`TraceExporter::send_trace_chunks`], bypassing msgpack
    /// encode/decode. The conversion runs here — on the background writer thread — so the
    /// per-request `put_trace` stays cheap; the GIL is released only for the network send.
    ///
    /// Returns ``(n_traces_sent, response_body)`` where ``response_body`` is the agent's JSON
    /// body for a changed sampling rate, else ``None``.
    fn flush(&self, py: Python<'_>) -> PyResult<(usize, Option<String>)> {
        let buffered = {
            let mut buf = self.lock_buffer();
            std::mem::take(&mut buf.chunks)
        };
        if buffered.is_empty() {
            return Ok((0, None));
        }

        // Packs meta_struct values; None skips meta_struct entirely.
        let packb = get_packb(py);
        let as_json = self.encode_links_events_as_json;

        let mut chunks: Vec<Vec<Span<PyTraceData>>> = Vec::with_capacity(buffered.len());
        for bc in &buffered {
            let origin: Option<PyBackedString> = match &bc.dd_origin {
                Some(o) => PyBackedString::try_from(o.bind(py).clone()).ok(),
                None => None,
            };
            let mut chunk: Vec<Span<PyTraceData>> = Vec::with_capacity(bc.spans.len());
            for span in &bc.spans {
                // Drop the SpanData borrow before calling packb (a GIL-yield point) — see
                // build_v04_span's doc comment.
                let (mut v04_span, meta_struct_raw) = {
                    let span_ref = span.bind(py).borrow();
                    span_ref.build_v04_span(py, origin.as_ref(), as_json, packb.is_some())?
                };
                if let Some(packb) = packb {
                    for (key, value) in meta_struct_raw {
                        let Ok(result) = packb.call1(py, (value.bind(py),)) else {
                            continue;
                        };
                        let Ok(py_bytes) = result.bind(py).cast::<PyBytes>() else {
                            continue;
                        };
                        v04_span
                            .meta_struct
                            .insert(key, Bytes::from_py_bytes(py_bytes));
                    }
                }
                chunk.push(v04_span);
            }
            chunks.push(chunk);
        }
        // Drop the buffered Python refs while the GIL is held (avoids deferring them).
        drop(buffered);

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

    /// Cyclic-GC traversal: the buffer holds live `Py<SpanData>`/`Py<PyString>` refs that can
    /// close reference cycles (span → context → tracer → writer → this exporter). Best-effort:
    /// if the buffer is momentarily locked, skip this cycle rather than risk blocking GC.
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        let guard = match self.buffer.try_lock() {
            Ok(g) => g,
            Err(std::sync::TryLockError::Poisoned(p)) => p.into_inner(),
            Err(std::sync::TryLockError::WouldBlock) => return Ok(()),
        };
        for chunk in &guard.chunks {
            for span in &chunk.spans {
                visit.call(span)?;
            }
            if let Some(origin) = &chunk.dd_origin {
                visit.call(origin)?;
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
