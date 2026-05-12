use std::fmt;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use libdd_capabilities_impl::NativeCapabilities;
use libdd_data_pipeline::{
    trace_buffer::{Export, ResponseHandler, TraceBuffer, TraceBufferConfig},
    trace_exporter::{agent_response::AgentResponse, error::TraceExporterError, TraceExporter},
};
use libdd_shared_runtime::{SharedRuntime, WorkerHandle};
use libdd_trace_utils::span::v04::Span;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyDict};

use crate::py_string::PyTraceData;
use crate::span::SpanData;

use super::TraceExporterPy;

/// Implements [Export] for [Span]<[PyTraceData]> by forwarding to a [TraceExporter].
///
/// Serialization happens inside `send_trace_chunks_async` using serde's `Serialize` impl on
/// `Span<PyTraceData>`. `PyBackedString::deref()` reads from a raw pointer that is safe to
/// call without the GIL because the pointer points into an immutable Python str object.
///
/// # GIL safety for cross-thread `Drop`
///
/// `Span<PyTraceData>` objects are moved into the tokio worker and may be dropped there
/// (after serialization) without the GIL. This is safe in pyo3 0.28+: `Py<T>::Drop`
/// checks `thread_is_attached()` and, when not on a Python-attached thread, enqueues the
/// pointer in the global `ReferencePool` (`Mutex<Vec<*mut PyObject>>`). The pool is
/// drained on the next `AttachGuard`/`SuspendAttach` transition on any thread that holds
/// the GIL. Source: `pyo3-0.28/src/instance.rs:2218-2244`, `src/internal/state.rs:186-217`.
#[derive(Debug)]
struct PyExport {
    exporter: TraceExporter<NativeCapabilities>,
}

impl Export<Span<PyTraceData>> for PyExport {
    fn export_trace_chunks(
        &mut self,
        trace_chunks: Vec<Vec<Span<PyTraceData>>>,
    ) -> Pin<
        Box<
            dyn std::future::Future<Output = Result<AgentResponse, TraceExporterError>> + Send + '_,
        >,
    > {
        Box::pin(async { self.exporter.send_trace_chunks_async(trace_chunks).await })
    }
}

/// Shared state that tracks whether an export has completed since the last `send_chunk` call.
///
/// `has_pending` is set to `true` by `send_chunk` and back to `false` by the
/// `ResponseHandler` after each export.  `export_gen` is a (mutex, condvar) pair whose
/// counter is incremented and whose condvar is notified after every export so that
/// `shutdown` can block (without the GIL) until the in-flight export finishes.
struct ExportSync {
    has_pending: AtomicBool,
    gen_lock: Mutex<u64>,
    gen_cvar: Condvar,
}

impl ExportSync {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            has_pending: AtomicBool::new(false),
            gen_lock: Mutex::new(0),
            gen_cvar: Condvar::new(),
        })
    }

    fn on_export_complete(&self) {
        self.has_pending.store(false, Ordering::Release);
        {
            let mut gen = self.gen_lock.lock().unwrap();
            *gen += 1;
        }
        self.gen_cvar.notify_one();
    }

    fn wait_for_export(&self, current_gen: u64, timeout: Duration) {
        let guard = self.gen_lock.lock().unwrap();
        let _ = self
            .gen_cvar
            .wait_timeout_while(guard, timeout, |g| *g <= current_gen);
    }
}

/// Python-facing `AgentResponse` object passed to the `response_callback` after each
/// successful export that returns a changed sampling-rate payload.
#[pyclass(name = "AgentResponse")]
pub struct AgentResponsePy {
    #[pyo3(get)]
    rate_by_service: Py<PyDict>,
}

#[pymethods]
impl AgentResponsePy {
    #[new]
    fn new(rate_by_service: Py<PyDict>) -> Self {
        Self { rate_by_service }
    }
}

/// Python-facing wrapper around a [TraceBuffer]<[Span]<[PyTraceData]>>.
///
/// `new` takes ownership of the [TraceExporter] from the given [TraceExporterPy]
/// (setting its inner to `None`) and spawns a background worker on the shared runtime.
#[pyclass(name = "NativeTraceBuffer")]
pub struct NativeTraceBufferPy {
    buffer: TraceBuffer<Span<PyTraceData>>,
    worker_handle: Option<WorkerHandle>,
    runtime: Arc<SharedRuntime>,
    export_sync: Arc<ExportSync>,
}

impl fmt::Debug for NativeTraceBufferPy {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("NativeTraceBufferPy").finish()
    }
}

#[pymethods]
impl NativeTraceBufferPy {
    /// Create a new NativeTraceBuffer.
    ///
    /// Takes ownership of the inner [TraceExporter] from `exporter` (after this call,
    /// `exporter.send()` will return an error). Spawns a background flush worker on
    /// `shared_runtime`.
    /// `response_callback` is an optional Python callable invoked after each successful export
    /// when the agent returns a changed sampling-rate payload. It receives one positional
    /// argument: `AgentResponse(rate_by_service=...)`. Only called when the agent response
    /// body contains a `rate_by_service` key.
    #[new]
    #[pyo3(signature = (exporter, response_callback = None))]
    fn new(exporter: &mut TraceExporterPy, response_callback: Option<Py<PyAny>>) -> PyResult<Self> {
        let inner = exporter
            .take_inner()
            .ok_or_else(|| PyValueError::new_err("TraceExporter has already been consumed"))?;
        let runtime = exporter.runtime_arc().clone();

        let export_sync = ExportSync::new();
        let export_sync_clone = export_sync.clone();

        let py_export = PyExport { exporter: inner };
        let response_handler: ResponseHandler = Box::new(move |result| {
            export_sync_clone.on_export_complete();
            if let Ok(AgentResponse::Changed { ref body }) = result {
                if let Some(ref cb) = response_callback {
                    invoke_response_callback(cb, body);
                }
            }
        });
        let config = TraceBufferConfig::default();

        let (buffer, worker) = TraceBuffer::new(config, response_handler, Box::new(py_export));

        let worker_handle = runtime.spawn_worker(worker, true).map_err(|e| {
            PyValueError::new_err(format!("Failed to spawn trace buffer worker: {e}"))
        })?;

        Ok(Self {
            buffer,
            worker_handle: Some(worker_handle),
            runtime,
            export_sync,
        })
    }

    /// Send a trace chunk (list of spans) to the buffer.
    ///
    /// Calls `SpanData::take_data` on each span to move attributes and meta_struct into
    /// the native span and clear the span's internal state. After this call, each span
    /// in the list is left in an empty/default state and must not be used further.
    fn send_chunk(&self, py: Python<'_>, spans: Vec<Py<SpanData>>) -> PyResult<()> {
        let packb = py
            .import("ddtrace.internal._encoding")
            .and_then(|m| m.getattr("packb"))
            .ok();
        let mut chunk: Vec<Span<PyTraceData>> = Vec::with_capacity(spans.len());
        for span in &spans {
            let mut span_ref = span.bind(py).borrow_mut();
            chunk.push(span_ref.take_data(py, packb.as_ref().map(|f| f.as_ref())));
        }
        // Set has_pending BEFORE handing the chunk to the buffer. If we set it after,
        // the tokio worker can pick up the chunk, export it, and fire on_export_complete
        // (clearing the flag) before our store(true) runs — leaving the flag stuck true
        // and causing shutdown() to block for the full timeout. Clearing on failure is safe:
        // it only races with on_export_complete, which already wrote false.
        self.export_sync.has_pending.store(true, Ordering::Release);
        let result = self.buffer.send_chunk(chunk);
        if result.is_err() {
            self.export_sync.has_pending.store(false, Ordering::Release);
        }
        result.map_err(|e| PyValueError::new_err(format!("TraceBuffer send_chunk error: {e:?}")))
    }

    /// Trigger a flush of the buffered spans without waiting for it to complete.
    fn force_flush(&self) -> PyResult<()> {
        self.buffer
            .force_flush()
            .map_err(|e| PyValueError::new_err(format!("TraceBuffer force_flush error: {e:?}")))
    }

    /// Flush and shut down the background worker, waiting up to `timeout_ns` nanoseconds.
    ///
    /// If there is pending (unsent) data this method releases the GIL and blocks until the
    /// background worker has completed the HTTP export, then stops the worker.  This
    /// avoids the race where the tokio `biased` select cancels the worker task before the
    /// in-flight export finishes.
    ///
    /// After this call the buffer cannot accept new spans.
    fn shutdown(&mut self, py: Python<'_>, timeout_ns: u64) -> PyResult<()> {
        let timeout = Duration::from_nanos(timeout_ns);

        // Snapshot the pending flag and generation *before* triggering the flush so that a
        // response_handler firing between the load and force_flush still counts.
        let has_pending = self.export_sync.has_pending.load(Ordering::Acquire);
        let current_gen = has_pending.then(|| *self.export_sync.gen_lock.lock().unwrap());

        if has_pending {
            let _ = self.buffer.force_flush();
        }

        let export_sync = self.export_sync.clone();
        let worker_handle = self.worker_handle.take();
        let runtime = self.runtime.clone();

        // Release the GIL for all blocking operations: waiting for the export to complete
        // and stopping the worker.
        py.detach(move || {
            if let Some(gen) = current_gen {
                export_sync.wait_for_export(gen, timeout);
            }
            if let Some(handle) = worker_handle {
                let _ = runtime.block_on(handle.stop());
            }
        });

        Ok(())
    }

    /// Wait until the background worker has shut down, up to `timeout_ns` nanoseconds.
    ///
    /// Intended for use in tests; prefer `shutdown` in production code.
    fn wait_shutdown_done(&self, timeout_ns: u64) -> PyResult<()> {
        self.buffer
            .wait_shutdown_done(Duration::from_nanos(timeout_ns))
            .map_err(|e| {
                PyValueError::new_err(format!("TraceBuffer wait_shutdown_done error: {e:?}"))
            })
    }
}

/// Deserialised form of the agent's sampling-rate response body.
/// Only `rate_by_service` is extracted; all other fields are ignored.
#[derive(serde::Deserialize)]
struct AgentRatesBody {
    rate_by_service: std::collections::HashMap<String, f64>,
}

/// Fire `response_callback(AgentResponse(rate_by_service=...))`.
///
/// Called from the [ResponseHandler] closure on the tokio worker thread.
/// Acquires the GIL, constructs an [AgentResponsePy], and calls the Python callback.
/// Errors are printed via `PyErr::print` — non-fatal.
fn invoke_response_callback(cb: &Py<PyAny>, body: &str) {
    let rates = match serde_json::from_str::<AgentRatesBody>(body) {
        Ok(r) => r.rate_by_service,
        Err(_) => return,
    };
    Python::attach(|py| {
        let call = || -> PyResult<()> {
            let py_rates = PyDict::new(py);
            for (k, v) in &rates {
                py_rates.set_item(k, v)?;
            }
            let resp = Py::new(py, AgentResponsePy { rate_by_service: py_rates.unbind() })?;
            cb.call1(py, (resp,))?;
            Ok(())
        };
        if let Err(e) = call() {
            e.print(py);
        }
    });
}

pub fn register_trace_buffer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<AgentResponsePy>()?;
    m.add_class::<NativeTraceBufferPy>()?;
    Ok(())
}
