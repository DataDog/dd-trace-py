use std::fmt;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use libdd_capabilities_impl::NativeCapabilities;
use libdd_data_pipeline::{
    trace_buffer::{Export, ResponseHandler, TraceBuffer, TraceBufferConfig},
    trace_exporter::{
        agent_response::AgentResponse, error::TraceExporterError, TraceExporter,
        TraceExporterBuilder, TraceExporterOutputFormat,
    },
};
use libdd_shared_runtime::{SharedRuntime, WorkerHandle};
use libdd_trace_utils::span::v04::Span;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyDict};

use crate::py_string::PyTraceData;
use crate::shared_runtime::SharedRuntimePy;
use crate::span::SpanData;

use super::agent_response::AgentResponsePy;

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

/// Python-facing wrapper around a [TraceBuffer]<[Span]<[PyTraceData]>>.
///
/// `new` builds a [TraceExporter] from the supplied config, then spawns a background
/// worker on the shared runtime.  This type has no dependency on [TraceExporterBuilderPy]
/// or [TraceExporterPy]: those classes can be removed without changing this code.
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
    /// Builds a [TraceExporter] from the supplied config params and spawns a background
    /// flush worker on `shared_runtime`.
    ///
    /// `response_callback` is an optional Python callable invoked after each successful export
    /// when the agent returns a changed sampling-rate payload. It receives one positional
    /// argument: `AgentResponse(rate_by_service=...)`. Only called when the agent response
    /// body contains a `rate_by_service` key.
    ///
    /// `api_version` must be `"v0.4"` or `"v0.5"`.
    ///
    /// `stats_interval_ns` is only used when `compute_stats_enabled=true` and
    /// `stats_opt_out=false`; if omitted it defaults to 10 seconds.
    #[new]
    #[pyo3(signature = (
        shared_runtime,
        intake_url,
        api_version,
        service = None,
        env = None,
        app_version = None,
        hostname = None,
        language_version = None,
        language_interpreter = None,
        tracer_version = None,
        git_commit_sha = None,
        compute_stats_enabled = false,
        stats_opt_out = false,
        stats_interval_ns = None,
        test_session_token = None,
        otlp_endpoint = None,
        response_callback = None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        shared_runtime: PyRef<'_, SharedRuntimePy>,
        intake_url: &str,
        api_version: &str,
        service: Option<&str>,
        env: Option<&str>,
        app_version: Option<&str>,
        hostname: Option<&str>,
        language_version: Option<&str>,
        language_interpreter: Option<&str>,
        tracer_version: Option<&str>,
        git_commit_sha: Option<&str>,
        compute_stats_enabled: bool,
        stats_opt_out: bool,
        stats_interval_ns: Option<u64>,
        test_session_token: Option<&str>,
        otlp_endpoint: Option<&str>,
        response_callback: Option<Py<PyAny>>,
    ) -> PyResult<Self> {
        let runtime = shared_runtime.as_arc().clone();

        // Build the TraceExporter from flat config params.  This type has no dependency on
        // TraceExporterBuilderPy or TraceExporterPy — those classes can be deleted without
        // touching this code.
        let output_format = match api_version {
            "v0.4" => TraceExporterOutputFormat::V04,
            "v0.5" => TraceExporterOutputFormat::V05,
            other => {
                return Err(PyValueError::new_err(format!(
                    "Invalid api_version {other:?}: expected \"v0.4\" or \"v0.5\""
                )))
            }
        };

        let mut builder = TraceExporterBuilder::default();
        builder.set_url(intake_url);
        builder.set_language("python");
        builder.set_client_computed_top_level();
        builder.set_output_format(output_format);
        if let Some(h) = hostname {
            builder.set_hostname(h);
        }
        if let Some(v) = language_version {
            builder.set_language_version(v);
        }
        if let Some(i) = language_interpreter {
            builder.set_language_interpreter(i);
        }
        if let Some(v) = tracer_version {
            builder.set_tracer_version(v);
        }
        if let Some(s) = git_commit_sha {
            builder.set_git_commit_sha(s);
        }
        if let Some(s) = service {
            builder.set_service(s);
        }
        if let Some(e) = env {
            builder.set_env(e);
        }
        if let Some(v) = app_version {
            builder.set_app_version(v);
        }
        if let Some(t) = test_session_token {
            builder.set_test_session_token(t);
        }
        if let Some(u) = otlp_endpoint {
            builder.set_otlp_endpoint(u);
        }
        if stats_opt_out {
            builder.set_client_computed_stats();
        } else if compute_stats_enabled {
            // Default to 10 s if the caller didn't supply an interval.
            let interval = Duration::from_nanos(stats_interval_ns.unwrap_or(10_000_000_000));
            builder.enable_stats(interval);
        }
        builder.set_shared_runtime(runtime.clone());

        let exporter = builder
            .build::<NativeCapabilities>()
            .map_err(|e| PyValueError::new_err(format!("TraceExporter build failed: {e}")))?;

        let export_sync = ExportSync::new();
        let export_sync_clone = export_sync.clone();

        let py_export = PyExport { exporter };
        let response_handler: ResponseHandler = Box::new(move |result| {
            export_sync_clone.on_export_complete();
            if let Ok(AgentResponse::Changed { ref body }) = result {
                if let Some(ref cb) = response_callback {
                    invoke_response_callback(cb, body);
                }
            }
        });
        let buffer_config = TraceBufferConfig::default();

        let (buffer, worker) =
            TraceBuffer::new(buffer_config, response_handler, Box::new(py_export));

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
    /// Calls `SpanData::to_native_span` on each span to snapshot its state into a
    /// libdatadog `Span<PyTraceData>` for encoding.  The `SpanData` fields are left
    /// intact so callers can still read span attributes after the chunk is sent.
    fn send_chunk(&self, py: Python<'_>, spans: Vec<Py<SpanData>>) -> PyResult<()> {
        let packb = py
            .import("ddtrace.internal._encoding")
            .and_then(|m| m.getattr("packb"))
            .ok();
        let mut chunk: Vec<Span<PyTraceData>> = Vec::with_capacity(spans.len());
        for span in &spans {
            let mut span_ref = span.bind(py).borrow_mut();
            chunk.push(span_ref.to_native_span(py, packb.as_ref()));
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
    ///
    /// # AIDEV-TODO: `WorkerHandle::stop()` needs timeout support in libdatadog
    ///
    /// `WorkerHandle::stop()` is an async fn with no timeout parameter.  If the underlying
    /// tokio worker is blocked on a network call (e.g. a slow or unresponsive trace agent)
    /// it will block indefinitely — there is no way for the caller to bound the wait.
    ///
    /// The right fix is to add `stop_with_timeout(duration: Duration)` (or make the existing
    /// `stop()` accept one) in libdatadog so callers don't have to work around this with
    /// fragile OS-thread spawning or unjoined threads.
    ///
    /// Until that lands, `stop()` is called directly.  In practice the network call is to
    /// a local trace agent over loopback and completes quickly; the theoretical hang only
    /// occurs when the agent is unreachable and the HTTP client has no connect/read timeout
    /// of its own.
    ///
    /// Tracking: https://datadoghq.atlassian.net/browse/APMLP-941
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
            let resp = Py::new(
                py,
                AgentResponsePy {
                    rate_by_service: py_rates.unbind(),
                },
            )?;
            cb.call1(py, (resp,))?;
            Ok(())
        };
        if let Err(e) = call() {
            e.print(py);
        }
    });
}

pub fn register_trace_buffer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NativeTraceBufferPy>()?;
    Ok(())
}
