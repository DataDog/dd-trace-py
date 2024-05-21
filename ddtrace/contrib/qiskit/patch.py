import time
import sys
from ddtrace import Span, config, tracer
from ddtrace.internal.schema import schematize_service_name
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ...vendor import wrapt
from ...internal.logger import get_logger

import qiskit
from qiskit.result import Result
import qiskit_ibm_runtime
from qiskit_ibm_runtime import RuntimeJob, RuntimeJobV2

log = get_logger(__name__)
config._add(
    "qiskit",
    {
        "distributed_tracing": True,
        "_default_service": schematize_service_name("qiskit"),
    },
)

_fake_run = qiskit_ibm_runtime.fake_provider.fake_backend.FakeBackendV2.run
_run = qiskit_ibm_runtime.IBMBackend.run
_sampler_run = qiskit_ibm_runtime.SamplerV2.run
_transpile = qiskit.transpile
_job = qiskit_ibm_runtime.QiskitRuntimeService.job
CURRENT_SPAN = "_ddtrace_current_span"
START_TIME = "_ddtrace_job_start_time"



def get_version():
    return qiskit.version.VERSION


def patch():
    """
    Patch `qiskit` module for tracing
    """
    # Check to see if we have patched qiskit yet or not
    if getattr(qiskit, "_datadog_patch", False):
        return
    setattr(qiskit, "_datadog_patch", True)
    
    qiskit_ibm_runtime.fake_provider.fake_backend.FakeBackendV2.run = wrapt.FunctionWrapper(
    _fake_run, traced_run
    )
    qiskit_ibm_runtime.IBMBackend.run = wrapt.FunctionWrapper(_run, traced_run)
    qiskit_ibm_runtime.SamplerV2.run = wrapt.FunctionWrapper(_sampler_run, traced_run)
    qiskit_ibm_runtime.QiskitRuntimeService.job = wrapt.FunctionWrapper(_job, traced_run)
    qiskit.transpile = wrapt.FunctionWrapper(_transpile, traced_transpile)
    Pin().onto(qiskit_ibm_runtime)
    
def unpatch():
    qiskit_ibm_runtime.fake_provider.fake_backend.FakeBackendV2.run = _run
    qiskit.transpile = _transpile


def traced_transpile(func, _, args, kwargs):
    with tracer.trace("qiskit.transpile"):
        return func(*args, **kwargs)

def traced_run(func, _, args, kwargs):
    job = func(*args, **kwargs)
    try:
        span = _start_span_with_tags(job)
        if isinstance(job, RuntimeJob) or isinstance(job, RuntimeJobV2):
            job.stream_results = wrapt.FunctionWrapper(
                job.stream_results, traced_stream_results
            )
        else:
            _close_span_on_success(job, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
    return job

def _start_span_with_tags(job):
    span = tracer.trace("qiskit.job")
    start_time = time.time()
    job_id = job.job_id()
    span.set_tag("job.start_time", start_time)
    setattr(job, START_TIME, start_time)
    span.set_tag("job.id", job_id)
    back = job.backend()
    span.set_tag("job.backend.name", back.name)
    span.set_tag("job.backend.provider", back.provider)
    span.set_tag("job.backend.version", back.version)
    if isinstance(job, RuntimeJobV2):
        span.set_tag("job.session_id", job.session_id)
        span.set_tag("job.program_id", job.program_id)
    if isinstance(job, RuntimeJob):
        span.set_tag("job.session_id", job.session_id)
        span.set_tag("job.program_id", job.program_id)
        span.set_tag("job.queue_position", job.queue_position(refresh=False))
    return span

def _close_span_on_success(job, span: Span):
    try:
        span.set_tag("job.status", job.status())
        stop_time = time.time()
        result = job.result()
        log.info(result)
        if isinstance(result, Result):
            span.set_tag("job.shots", getattr(result.results[0], "shots", 0))
            span.set_tag("job.name", getattr(result.results[0], "name", ""))
            span.set_tag("job.duration", result.time_taken)
        elapsed_time = stop_time - getattr(job, START_TIME) - result.time_taken
        if elapsed_time < 0:
            elapsed_time = 0
        span.set_tag("job.wait_time", elapsed_time)
    except Exception:
        log.debug("an exception occurred while setting tags", exc_info=True)
    finally:
        span.finish()
        delattr(job, START_TIME)

def traced_stream_results(func, _, args, kwargs):
    log.info("traced_stream_results")
    callback = args[0]
    def traced_callback(*args, **kwargs):
        log.info("traced_stream_results_callback")
        return callback(*args, **kwargs)

    return func(traced_callback, *args[1:], **kwargs)