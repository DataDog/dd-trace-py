//! Python binding for libdatadog's bounded trace buffer.
//!
//! The buffer is the data plane of an opt-in trace writer: Python hands it finished spans, and a
//! libdatadog worker thread serializes and sends them. A thin Python class owns the configuration
//! and the lifecycle, including the rebuild after a fork.

use std::fmt::Debug;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use libdd_capabilities_impl::NativeCapabilities;
use libdd_data_pipeline::trace_buffer::{
    Export, ResponseHandler, TraceBuffer, TraceBufferConfig, TraceBufferError, TraceChunk,
};
use libdd_data_pipeline::trace_exporter::{
    agent_response::AgentResponse, error::TraceExporterError, TraceExporter,
};
use libdd_shared_runtime::{
    BlockingRuntime as _, ForkSafeRuntime, SharedRuntime as _, WorkerHandle,
};
use libdd_trace_utils::span::v04::Span;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;

use super::{TraceExporterBuilderPy, TraceExporterOutputFormat};
use crate::py_string::{PyBackedString, PyTraceData};
use crate::shared_runtime::SharedRuntimePy;
use crate::span::wire::build_wire_span;
use crate::span::SpanData;

/// The wire span the buffer stores. Every string in it is a Python object the span keeps alive, so
/// the worker thread reads the whole span without the GIL.
type PySpan = Span<PyTraceData>;

fn buffer_err_to_pyerr(err: TraceBufferError) -> PyErr {
    PyRuntimeError::new_err(format!("{err:?}"))
}

/// Sends chunks of Python-backed spans through a [`TraceExporter`].
///
/// libdatadog's `DefaultExport` only accepts `SpanBytes`, so the Python wire span needs its own
/// [`Export`] implementation.
#[derive(Debug)]
struct SpanExport {
    exporter: TraceExporter<NativeCapabilities, ForkSafeRuntime>,
}

impl Export<PySpan> for SpanExport {
    fn export_trace_chunks(
        &mut self,
        trace_chunks: Vec<TraceChunk<PySpan>>,
    ) -> Pin<Box<dyn Future<Output = Result<AgentResponse, TraceExporterError>> + Send + '_>> {
        Box::pin(async move { self.exporter.send_trace_chunks_async(trace_chunks).await })
    }
}

/// A bounded queue of trace chunks drained by a libdatadog worker.
#[pyclass(name = "NativeTraceBuffer", module = "ddtrace.internal._native")]
pub struct NativeTraceBuffer {
    buffer: TraceBuffer<PySpan>,
    /// Drives the worker. `shutdown` needs it to block on the worker's async stop.
    runtime: Arc<ForkSafeRuntime>,
    /// Taken by `shutdown`. While it is `Some` the worker stays registered on the runtime, so a
    /// caller that drops this object without calling `shutdown` keeps the worker alive until the
    /// runtime itself shuts down.
    worker: Option<WorkerHandle>,
    /// Written by the response handler on a worker thread, drained by `take_agent_response`.
    agent_response: Arc<Mutex<Option<String>>>,
    mask_link_flags: bool,
}

impl NativeTraceBuffer {
    fn spawn(
        runtime: Arc<ForkSafeRuntime>,
        exporter: TraceExporter<NativeCapabilities, ForkSafeRuntime>,
        config: TraceBufferConfig,
        mask_link_flags: bool,
    ) -> PyResult<Self> {
        let agent_response = Arc::new(Mutex::new(None));
        let slot = Arc::clone(&agent_response);
        // The handler runs on a worker thread and must touch nothing owned by Python. A fork holds
        // the GIL on the forking thread while it waits for the workers to pause, so a handler that
        // attached to Python would block on the GIL and deadlock the fork. It parks the response
        // body in a plain mutex instead, and Python drains it later.
        let handler: ResponseHandler = Box::new(move |result| match result {
            Ok(AgentResponse::Changed { body }) => {
                if let Ok(mut slot) = slot.lock() {
                    *slot = Some(body);
                }
            }
            Ok(AgentResponse::Unchanged) => {}
            Err(err) => tracing::error!("native trace buffer export failed: {err}"),
        });

        let (buffer, worker) = TraceBuffer::new(config, handler, Box::new(SpanExport { exporter }));
        // restart_on_fork is false because libdatadog's restart path calls `worker.reset()`, which
        // takes the buffer state mutex with nothing holding off the threads that may own it. The
        // child therefore drops the worker without a shutdown, and every span still buffered at the
        // moment of the fork is lost. The Python layer rebuilds the buffer after a fork, so the
        // child never sends from inherited state.
        let worker = runtime
            .spawn_worker(worker, false)
            .map_err(|err| PyRuntimeError::new_err(format!("{err}")))?;

        Ok(Self {
            buffer,
            runtime,
            worker: Some(worker),
            agent_response,
            mask_link_flags,
        })
    }
}

#[pymethods]
impl NativeTraceBuffer {
    /// Consume `builder` and start a buffer with its own worker.
    ///
    /// `builder` carries the exporter configuration only. The buffer owns the flush cadence, the byte
    /// bounds and the drop policy, so `TraceBufferConfig` carries those instead. Pass None for any of
    /// them to keep libdatadog's default.
    ///
    /// The builder cannot be reused, the same as after `build`.
    #[new]
    #[pyo3(signature = (
        builder,
        shared_runtime,
        max_buffered_bytes = None,
        flush_threshold_bytes = None,
        max_flush_interval_ns = None,
    ))]
    fn new(
        mut builder: PyRefMut<'_, TraceExporterBuilderPy>,
        shared_runtime: PyRef<'_, SharedRuntimePy>,
        max_buffered_bytes: Option<usize>,
        flush_threshold_bytes: Option<usize>,
        max_flush_interval_ns: Option<u64>,
    ) -> PyResult<Self> {
        // A v0.5 output makes the exporter convert the v0.4 span, and that conversion copies the
        // span-link flags verbatim, so the "flags present" bit must not reach it.
        let mask_link_flags = builder.output_format == TraceExporterOutputFormat::V05;

        let runtime = shared_runtime.as_arc().clone();
        let inner = builder
            .builder
            .take()
            .ok_or_else(|| PyValueError::new_err("Builder has already been consumed"))?;
        let exporter = {
            let mut inner = inner;
            inner.set_shared_runtime(runtime.clone());
            // The exporter's own workers must not restart in a forked child. dd-trace-py rebuilds
            // this buffer, and the exporter with it, from the post-fork hook, so a restart here would
            // duplicate workers and run libdatadog's reset path for nothing. libdatadog added this
            // setter for that reason: see APMSP-3846 on `set_restart_after_fork`.
            inner.set_restart_after_fork(false);
            inner
                .build::<NativeCapabilities>()
                .map_err(|err| PyValueError::new_err(format!("Builder {err}")))?
        };

        let mut config = TraceBufferConfig::new();
        if let Some(max) = max_buffered_bytes {
            config = config.max_buffered_bytes(max);
        }
        if let Some(threshold) = flush_threshold_bytes {
            config = config.flush_threshold_bytes(threshold);
        }
        if let Some(interval) = max_flush_interval_ns {
            config = config.max_flush_interval(Duration::from_nanos(interval));
        }

        Self::spawn(runtime, exporter, config, mask_link_flags)
    }

    /// Project `spans` into wire spans and enqueue them as one trace chunk.
    ///
    /// Returns None when the buffer accepted every span, and otherwise a reason. This method never
    /// raises: `Span.finish()` calls it on an application thread inside a `with tracer.trace(...)`
    /// block, where an exception would escape into user code.
    ///
    /// `dd_origin` is the trace-level origin, injected into every span of the chunk.
    #[pyo3(signature = (spans, dd_origin = None))]
    fn write(
        &self,
        py: Python<'_>,
        spans: &Bound<'_, PyList>,
        dd_origin: Option<&Bound<'_, PyAny>>,
    ) -> Option<String> {
        // Extract leniently. `Context.dd_origin` does not validate what it stores, so it can hold any
        // object, and a typed parameter would make PyO3 raise from the argument layer before this
        // body runs. Drop a non-string origin instead.
        let dd_origin: Option<PyBackedString> = dd_origin
            .filter(|o| !o.is_none())
            .and_then(|o| o.extract::<PyBackedString>().ok());
        let mut chunk: Vec<PySpan> = Vec::with_capacity(spans.len());
        let mut skipped: usize = 0;
        for item in spans.iter() {
            let Ok(span) = item.cast::<SpanData>() else {
                skipped += 1;
                continue;
            };
            // A span that some other frame still borrows mutably would make `borrow` panic, and a
            // panic reaches Python as a PanicException that `except Exception` does not catch.
            let Ok(span) = span.try_borrow() else {
                skipped += 1;
                continue;
            };
            chunk.push(build_wire_span(
                py,
                &span,
                dd_origin.as_ref(),
                self.mask_link_flags,
            ));
        }

        // The GIL stays held: `send_chunk` only takes the buffer's state mutex and notifies the
        // worker, because synchronous export is off.
        if let Err(err) = self.buffer.send_chunk(chunk) {
            return Some(format!("{err:?}"));
        }
        if skipped > 0 {
            return Some(format!(
                "{skipped} spans were not readable and were dropped"
            ));
        }
        None
    }

    /// Ask the worker to export what is buffered. Returns before the export completes.
    fn force_flush(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.buffer.force_flush())
            .map_err(buffer_err_to_pyerr)
    }

    /// Flush, stop the worker, and wait up to `timeout_ns` for the worker to report shutdown.
    ///
    /// `timeout_ns` bounds only the wait for that report. Stopping the worker first waits for an
    /// export already in flight, which the exporter's own request timeout bounds.
    ///
    /// A second call is a no-op.
    fn shutdown(&mut self, py: Python<'_>, timeout_ns: u64) -> PyResult<()> {
        let Some(worker) = self.worker.take() else {
            return Ok(());
        };
        let runtime = Arc::clone(&self.runtime);
        // The GIL is released for the whole sequence: it ends in a network send on the worker.
        py.detach(move || {
            // Best effort: the worker exports the pending batch only if it wakes before its trigger
            // is cancelled.
            let _ = self.buffer.force_flush();
            runtime
                .block_on(worker.stop())
                .map_err(|err| PyRuntimeError::new_err(format!("{err}")))?
                .map_err(|err| PyRuntimeError::new_err(format!("{err}")))?;
            self.buffer
                .wait_shutdown_done(Duration::from_nanos(timeout_ns))
                .map_err(buffer_err_to_pyerr)
        })
    }

    /// Take the body of the most recent changed agent response, leaving the slot empty.
    fn take_agent_response(&self) -> Option<String> {
        let mut slot = self.agent_response.lock().ok()?;
        slot.take()
    }

    /// Return (spans_dropped_full_buffer, spans_queued) counted since the previous call.
    ///
    /// libdatadog resets both counters on read, so a caller that discards the result loses those
    /// counts for good.
    fn queue_metrics(&self) -> (usize, usize) {
        let metrics = self.buffer.queue_metrics().get_metrics();
        (metrics.spans_dropped_full_buffer, metrics.spans_queued)
    }
}
