//! Python binding for libdatadog's bounded trace buffer.
//!
//! The buffer is the data plane of an opt-in trace writer: Python hands it finished spans, and a
//! libdatadog worker thread serializes and sends them. A thin Python class owns the configuration
//! and the lifecycle, including the rebuild after a fork.

use std::fmt::Debug;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, Instant};

use libdd_capabilities_impl::NativeCapabilities;
use libdd_data_pipeline::trace_buffer::{
    Export, ResponseHandler, TraceBuffer, TraceBufferConfig, TraceBufferError, TraceChunk,
};
use libdd_data_pipeline::trace_exporter::{
    agent_response::AgentResponse,
    error::{InternalErrorKind, TraceExporterError},
    TraceExporter,
};
use libdd_shared_runtime::{
    BlockOnTimeoutError, BlockingRuntime as _, ForkSafeRuntime, SharedRuntime as _, WorkerHandle,
};
use libdd_trace_utils::span::v04::Span;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

use super::TraceExporterBuilderPy;
use crate::py_string::{PyBackedString, PyTraceData};
use crate::shared_runtime::SharedRuntimePy;
use crate::span::wire::build_wire_span;
use crate::span::SpanData;

/// The wire span the buffer stores. Every string in it is a Python object the span keeps alive, so
/// the worker thread reads the whole span without the GIL.
type PySpan = Span<PyTraceData>;

type Exporter = TraceExporter<NativeCapabilities, ForkSafeRuntime>;

fn buffer_err_to_pyerr(err: TraceBufferError) -> PyErr {
    PyRuntimeError::new_err(format!("{err:?}"))
}

/// Sends chunks of Python-backed spans through a [`TraceExporter`].
///
/// libdatadog's `DefaultExport` only accepts `SpanBytes`, so the Python wire span needs its own
/// [`Export`] implementation.
///
/// The reference is weak, and [`TraceBufferPy`] holds the only strong one. `TraceExporter::shutdown`
/// consumes the exporter and `Export` has no shutdown hook, so an exporter owned from here could never
/// be shut down, and the workers it owns would stay registered for the life of the process.
#[derive(Debug)]
struct SpanExport {
    exporter: Weak<Exporter>,
}

impl Export<PySpan> for SpanExport {
    fn export_trace_chunks(
        &mut self,
        trace_chunks: Vec<TraceChunk<PySpan>>,
    ) -> Pin<Box<dyn Future<Output = Result<AgentResponse, TraceExporterError>> + Send + '_>> {
        // Upgrade before the future, so the exporter cannot be reclaimed part way through a send.
        let exporter = self.exporter.upgrade();
        Box::pin(async move {
            let Some(exporter) = exporter else {
                return Err(TraceExporterError::Internal(
                    InternalErrorKind::InvalidWorkerState(
                        "the trace exporter is already shut down".to_string(),
                    ),
                ));
            };
            exporter.send_trace_chunks_async(trace_chunks).await
        })
    }
}

/// A bounded queue of trace chunks drained by a libdatadog worker.
#[pyclass(name = "TraceBuffer", frozen, module = "ddtrace.internal._native")]
pub struct TraceBufferPy {
    buffer: TraceBuffer<PySpan>,
    /// Drives the worker. `shutdown` needs it to block on the worker's async stop.
    runtime: Arc<ForkSafeRuntime>,
    /// Taken by `shutdown`. While it is `Some` the worker stays registered on the runtime, so a
    /// caller that drops this object without calling `shutdown` keeps the worker alive until the
    /// runtime itself shuts down.
    ///
    /// A `Mutex` rather than a plain field, because the pyclass is frozen. Taking `&mut self` here
    /// instead would make PyO3 hold a mutable borrow for the whole of `shutdown`, including its
    /// detached network wait, and every concurrent `write()` would then raise `RuntimeError` from the
    /// argument layer, where `write` cannot absorb it.
    worker: Mutex<Option<WorkerHandle>>,
    /// The exporter the buffer sends through, and the only strong reference to it.
    ///
    /// `shutdown` needs it because the exporter owns up to four workers of its own — agent-info,
    /// telemetry, dogstatsd and the OTLP stats exporter — and `TraceExporter::shutdown` is the only
    /// thing that reclaims them. That call consumes the exporter, hence the `Arc` this method can
    /// unwrap and the `Weak` inside [`SpanExport`].
    ///
    /// A `Mutex` for the same reason as `worker`: the pyclass is frozen.
    exporter: Mutex<Option<Arc<Exporter>>>,
    /// Written by the response handler on a worker thread, drained by `take_agent_response`.
    agent_response: Arc<Mutex<Option<String>>>,
}

impl TraceBufferPy {
    fn spawn(
        runtime: Arc<ForkSafeRuntime>,
        exporter: Exporter,
        config: TraceBufferConfig,
    ) -> PyResult<Self> {
        let agent_response = Arc::new(Mutex::new(None));
        let slot = Arc::clone(&agent_response);
        // The handler runs on a worker thread and must touch nothing owned by Python.
        // `SharedRuntime.shutdown` blocks on the workers with the GIL held, so a handler that
        // attached to Python would wait for a GIL that the shutting-down thread only releases once
        // the workers stop. It parks the response body in a plain mutex instead, and Python drains
        // it later.
        let handler: ResponseHandler = Box::new(move |result| match result {
            Ok(AgentResponse::Changed { body }) => {
                if let Ok(mut slot) = slot.lock() {
                    *slot = Some(body);
                }
            }
            Ok(AgentResponse::Unchanged) => {}
            Err(err) => tracing::error!("native trace buffer export failed: {err}"),
        });

        let exporter = Arc::new(exporter);
        let (buffer, worker) = TraceBuffer::new(
            config,
            handler,
            Box::new(SpanExport {
                exporter: Arc::downgrade(&exporter),
            }),
        );
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
            worker: Mutex::new(Some(worker)),
            exporter: Mutex::new(Some(exporter)),
            agent_response,
        })
    }

    /// Shut the exporter down, reclaiming the workers it owns.
    ///
    /// The caller must have stopped the buffer's worker first: an export in flight holds a strong
    /// reference, and the unwrap then fails.
    fn shutdown_exporter(exporter: Option<Arc<Exporter>>, timeout: Duration) -> PyResult<()> {
        let Some(exporter) = exporter else {
            return Ok(());
        };
        let Some(exporter) = Arc::into_inner(exporter) else {
            return Err(PyRuntimeError::new_err(
                "the trace exporter is still exporting and cannot be shut down",
            ));
        };
        exporter
            .shutdown(Some(timeout))
            .map_err(|err| PyRuntimeError::new_err(format!("{err}")))
    }
}

#[pymethods]
impl TraceBufferPy {
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
        synchronous_export = false,
        synchronous_export_timeout_ns = None,
    ))]
    fn new(
        mut builder: PyRefMut<'_, TraceExporterBuilderPy>,
        shared_runtime: PyRef<'_, SharedRuntimePy>,
        max_buffered_bytes: Option<usize>,
        flush_threshold_bytes: Option<usize>,
        max_flush_interval_ns: Option<u64>,
        synchronous_export: bool,
        synchronous_export_timeout_ns: Option<u64>,
    ) -> PyResult<Self> {
        // A v0.5 output makes the exporter convert the v0.4 span, and that conversion copies the
        // span-link flags verbatim, so the "flags present" bit must not reach it.

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
            // duplicate workers and run libdatadog's reset path for nothing. See APMSP-3846.
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
        // Synchronous export makes `send_chunk` block until the worker has exported the batch. A
        // caller that can be frozen or killed right after a span finishes needs it, because nothing
        // else guarantees delivery: serverless hosts stop the process between requests.
        if synchronous_export {
            config = config.synchronous_export(true).synchronous_export_timeout(
                synchronous_export_timeout_ns.map(Duration::from_nanos),
            );
        }

        Self::spawn(runtime, exporter, config)
    }

    /// Project `spans` into wire spans and enqueue them as one trace chunk.
    ///
    /// Returns None when the buffer accepted every span, and otherwise a reason. This method never
    /// raises: `Span.finish()` calls it on an application thread inside a `with tracer.trace(...)`
    /// block, where an exception would escape into user code.
    ///
    /// `dd_origin` is the trace-level origin, injected into every span of the chunk.
    ///
    /// `spans` is any iterable. A typed parameter would make PyO3 raise from the argument layer, and
    /// the caller has no list to promise: `SpanAggregator.on_span_finish` forwards whatever a user's
    /// `TraceProcessor.process_trace` returned.
    #[pyo3(signature = (spans, dd_origin = None))]
    fn write(
        &self,
        py: Python<'_>,
        spans: &Bound<'_, PyAny>,
        dd_origin: Option<&Bound<'_, PyAny>>,
    ) -> Option<String> {
        // Extract leniently. `Context.dd_origin` does not validate what it stores, so it can hold any
        // object, and a typed parameter would make PyO3 raise from the argument layer before this
        // body runs. Drop a non-string origin instead.
        let dd_origin: Option<PyBackedString> = dd_origin
            .filter(|o| !o.is_none())
            .and_then(|o| o.extract::<PyBackedString>().ok());
        let Ok(items) = spans.try_iter() else {
            return Some("spans is not iterable".to_string());
        };
        // `len` fails on a generator, and the chunk then grows by reallocation.
        let mut chunk: Vec<PySpan> = Vec::with_capacity(spans.len().unwrap_or(0));
        let mut skipped: usize = 0;
        for item in items {
            // An iterable that raises part way through leaves the rest of the trace behind. Stop
            // there and send what is already built, rather than lose the whole chunk.
            let Ok(item) = item else {
                skipped += 1;
                break;
            };
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
            chunk.push(build_wire_span(py, &span, dd_origin.as_ref()));
        }

        // In synchronous mode `send_chunk` blocks until the worker has exported the batch, which is
        // the point: a serverless host can freeze the process the moment a request ends, so the
        // request must not finish before the trace is on the wire. Release the GIL for that wait,
        // otherwise one blocking write stalls every other thread in the interpreter.
        if let Err(err) = py.detach(|| self.buffer.send_chunk(chunk)) {
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

    /// Flush what is buffered and block until the worker exports it, or until `timeout_ns`
    /// elapses.
    ///
    /// `timeout_ns` of 0 only triggers the flush, the same as `force_flush`: libdatadog's own
    /// zero-timeout wait would report `TimedOut` for a flush that a moment later still succeeds,
    /// which a caller could mistake for lost data.
    ///
    /// This leaves the buffer usable: a later call can flush again.
    ///
    /// # Errors
    /// * `TimedOut` -- the export did not finish in time. The data is still queued, and the
    ///   worker keeps trying to export it.
    /// * `AlreadyClosed` -- the buffer no longer accepts chunks, or a concurrent close ended
    ///   the wait. The shutdown drain can still export the data that was buffered.
    fn flush(&self, py: Python<'_>, timeout_ns: u64) -> PyResult<()> {
        py.detach(|| {
            if timeout_ns == 0 {
                self.buffer.force_flush()
            } else {
                self.buffer
                    .flush_and_wait(Some(Duration::from_nanos(timeout_ns)))
            }
        })
        .map_err(buffer_err_to_pyerr)
    }

    /// Flush what is buffered, stop the worker, and shut the exporter down, within `timeout_ns`.
    ///
    /// The budget splits across three network-bound steps: flushing the current batch, stopping
    /// the worker (which waits out an export already in flight), and shutting the exporter's own
    /// workers down. A step that runs over gives up only its own wait, not the caller's whole
    /// budget, so this returns close to `timeout_ns` even against an agent that never answers.
    ///
    /// A second call is a no-op.
    fn shutdown(&self, py: Python<'_>, timeout_ns: u64) -> PyResult<()> {
        let taken = self.worker.lock().unwrap_or_else(|e| e.into_inner()).take();
        let Some(worker) = taken else {
            return Ok(());
        };
        let exporter = self
            .exporter
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take();
        let runtime = Arc::clone(&self.runtime);
        let total = Duration::from_nanos(timeout_ns);
        // Reserve a slice for the exporter's own shutdown up front, so a slow flush or a slow
        // worker stop cannot starve it: nothing else reclaims the exporter's four workers, and an
        // abandoned wait there would leak them for the life of the process.
        let exporter_reserve =
            (total / 5).clamp(Duration::from_millis(500), Duration::from_secs(2));
        let remaining = total.saturating_sub(exporter_reserve);
        let flush_budget = remaining / 2;

        // The GIL is released for the whole sequence: it ends in a network send on the worker.
        py.detach(move || {
            let start = Instant::now();
            // Best effort: `flush_and_wait` reports `TimedOut` for a flush that a moment later
            // still succeeds, and stopping the worker below waits out the same in-flight export
            // regardless, so a timeout here does not mean the shutdown itself failed.
            let _ = self.buffer.flush_and_wait(Some(flush_budget));

            let stop_budget = remaining.saturating_sub(start.elapsed());
            let stopped = match runtime.block_on_with_timeout(worker.stop(), stop_budget) {
                // The runtime itself could not drive the future, not the worker.
                Err(BlockOnTimeoutError::Io(io_err)) => {
                    Err(PyRuntimeError::new_err(format!("{io_err}")))
                }
                // `worker.stop()` did not finish in time. It never reached `Worker::shutdown`, so
                // the buffer may still accept chunks that no drain will ever export: close it now.
                // The flush above already sent what was buffered before this call started.
                Err(BlockOnTimeoutError::TimedOut(_)) => {
                    let _ = self.buffer.flush_and_close(Some(Duration::ZERO));
                    Ok(())
                }
                Ok(result) => result.map_err(|err| PyRuntimeError::new_err(format!("{err}"))),
            };

            // However long the steps above took, the exporter still gets its reserved slice, not
            // whatever is left over.
            let exporter_budget = total.saturating_sub(start.elapsed()).max(exporter_reserve);
            let exporter_down = Self::shutdown_exporter(exporter, exporter_budget);

            stopped.and(exporter_down)
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
