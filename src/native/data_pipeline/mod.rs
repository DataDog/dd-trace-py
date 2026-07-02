use libdd_capabilities_impl::NativeCapabilities;
use libdd_data_pipeline::trace_buffer::BufferSize;
use libdd_data_pipeline::trace_exporter::{
    agent_response::AgentResponse, TelemetryConfig, TraceExporter, TraceExporterBuilder,
    TraceExporterInputFormat, TraceExporterOutputFormat,
};
use libdd_trace_utils::span::v04::Span;
use pyo3::types::PyString;
use pyo3::{exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes};
use std::sync::Mutex;
use std::time::Duration;
mod agent_response;
mod exceptions;
use crate::py_string::{PyBackedString, PyTraceData};
use crate::shared_runtime::SharedRuntimePy;
use crate::span::SpanData;
use exceptions::TraceExporterErrorPy;

/// A wrapper around [TraceExporterBuilder]
///
/// Allows to use the builder as a python class. Only one exporter can be built using a builder
/// once `build` has been called the builder shouldn't be reused.
#[pyclass(name = "TraceExporterBuilder")]
pub struct TraceExporterBuilderPy {
    builder: Option<TraceExporterBuilder>,
}

impl TraceExporterBuilderPy {
    fn try_as_mut(&mut self) -> PyResult<&mut TraceExporterBuilder> {
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
    ///
    /// `max_size` / `max_item_size` bound the native span buffer (in estimated bytes):
    /// `max_size` is the total budget across all buffered trace chunks, `max_item_size`
    /// the cap for a single chunk. They mirror the writer's `buffer_size` /
    /// `max_payload_size` settings.
    fn build(
        &mut self,
        shared_runtime: PyRef<'_, SharedRuntimePy>,
        max_size: usize,
        max_item_size: usize,
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
            buffer: Mutex::new(TraceBuffer {
                chunks: Vec::new(),
                est_bytes: 0,
                max_size,
                max_item_size,
            }),
        };
        Ok(exporter)
    }

    fn debug(&self) -> String {
        format!("{:?}", self.builder)
    }
}

/// Native buffer of converted libdatadog v0.4 spans awaiting flush.
///
/// Holds `Vec<Vec<Span<PyTraceData>>>` — a list of trace chunks, each a list of spans —
/// exactly the shape [`TraceExporter::send_trace_chunks`] consumes. `est_bytes` tracks the
/// approximate serialized size (via [`BufferSize::byte_size`]) for `max_size` enforcement.
struct TraceBuffer {
    chunks: Vec<Vec<Span<PyTraceData>>>,
    est_bytes: usize,
    max_size: usize,
    max_item_size: usize,
}

/// Outcome of [`TraceExporterPy::put_trace`], mirroring the Cython encoder's drop reasons
/// so the Python writer can emit the same `buffer.dropped.*` metrics without exceptions on
/// the hot path.
#[pyclass(name = "PutOutcome", eq, eq_int, skip_from_py_object)]
#[derive(PartialEq, Clone, Copy, Debug)]
pub enum PutOutcome {
    Accepted,
    BufferFull,
    ItemTooLarge,
    NoEncodableSpans,
}

/// A python object wrapping a [TraceExporter] instance plus a native span buffer.
#[pyclass(name = "TraceExporter")]
pub struct TraceExporterPy {
    inner: Option<TraceExporter<NativeCapabilities>>,
    buffer: Mutex<TraceBuffer>,
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

    /// Convert one trace chunk (a Python ``list[Span]``) into libdatadog v0.4 spans and
    /// buffer it for the next flush.
    ///
    /// Each element must be a ``SpanData`` (the Python ``Span`` subclasses it), letting us
    /// borrow the native struct directly and read its attributes without the Python C-API.
    ///
    /// ``dd_origin`` is the trace-level origin (``trace[0].context.dd_origin``, extracted on
    /// the Python side); it is stamped as ``_dd.origin`` into every span's meta.
    ///
    /// Returns a [`PutOutcome`] (never raises for buffer pressure) so the writer can record
    /// drop metrics on the hot path without exception handling.
    #[pyo3(signature = (spans, dd_origin=None))]
    fn put_trace(
        &self,
        py: Python<'_>,
        spans: Vec<Py<SpanData>>,
        dd_origin: Option<Bound<'_, PyString>>,
    ) -> PyResult<PutOutcome> {
        if spans.is_empty() {
            return Ok(PutOutcome::NoEncodableSpans);
        }

        let origin = match dd_origin {
            Some(s) => Some(PyBackedString::try_from(s)?),
            None => None,
        };

        // The vendored msgpack packer, used only to serialize meta_struct values. Imported
        // once per chunk (a cheap sys.modules lookup); `None` simply skips meta_struct.
        let packb = py
            .import("ddtrace.internal._encoding")
            .and_then(|m| m.getattr("packb"))
            .ok();

        let mut chunk: Vec<Span<PyTraceData>> = Vec::with_capacity(spans.len());
        for span in &spans {
            let span_ref = span.bind(py).borrow();
            chunk.push(span_ref.build_v04_span(py, packb.as_ref(), origin.as_ref()));
        }
        let item_bytes: usize = chunk.iter().map(|s| s.byte_size()).sum();

        let mut buf = self.buffer.lock().unwrap();
        if item_bytes > buf.max_item_size {
            return Ok(PutOutcome::ItemTooLarge);
        }
        if buf.est_bytes + item_bytes > buf.max_size {
            return Ok(PutOutcome::BufferFull);
        }
        buf.chunks.push(chunk);
        buf.est_bytes += item_bytes;
        Ok(PutOutcome::Accepted)
    }

    /// Drain the buffered trace chunks and send them directly to the agent via
    /// [`TraceExporter::send_trace_chunks`], bypassing msgpack encode/decode entirely.
    ///
    /// The GIL is released for the whole send: serialization reads ``PyBackedString`` via a
    /// raw pointer (GIL-free), and the consumed spans' ``Py`` refs are dropped off-GIL using
    /// pyo3's deferred-decref (drained on reattach).
    ///
    /// Returns ``(n_traces_sent, response_body)`` where ``response_body`` is the agent's
    /// JSON body for a changed sampling rate, else ``None``.
    fn flush(&self, py: Python<'_>) -> PyResult<(usize, Option<String>)> {
        let chunks = {
            let mut buf = self.buffer.lock().unwrap();
            buf.est_bytes = 0;
            std::mem::take(&mut buf.chunks)
        };
        let n = chunks.len();
        if n == 0 {
            return Ok((0, None));
        }
        let res: PyResult<AgentResponse> = py.detach(move || {
            let exporter = self
                .inner
                .as_ref()
                .ok_or_else(|| PyValueError::new_err("TraceExporter has already been consumed"))?;
            exporter
                .send_trace_chunks(chunks)
                .map_err(|e| TraceExporterErrorPy::from(e).into())
        });
        match res? {
            AgentResponse::Changed { body } => Ok((n, Some(body))),
            AgentResponse::Unchanged => Ok((n, None)),
        }
    }

    /// Number of trace chunks currently buffered (replaces ``len(encoder)`` for drop-rate).
    fn buffered_traces(&self) -> usize {
        self.buffer.lock().unwrap().chunks.len()
    }

    /// Estimated bytes currently buffered (replaces ``encoder.size``).
    fn buffered_bytes(&self) -> usize {
        self.buffer.lock().unwrap().est_bytes
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
