# -*- encoding: utf-8 -*-
import logging
from ddtrace.internal.datadog.profiling import ddup


LOG = logging.getLogger(__name__)


def handle_torch_trace(self, prof):
    NANOS_PER_MICROSECOND = 1e3
    LOG.debug("handle torch trace was called")
    kineto_events = prof.profiler.kineto_results.events()
    trace_start_us = prof.profiler.kineto_results.trace_start_us()
    for i, e in enumerate(prof.events()):
        device_name = "cuda " + str(e.device_index)
        end_time_us= int(trace_start_us + e.time_range.end)
        event_duration_us = e.time_range.elapsed_us()
        if str(e.device_type).startswith("DeviceType.CUDA") and i % 10 == 0:
            # gpu time sample
            handle = ddup.SampleHandle()
            handle.push_gputime(event_duration_us * NANOS_PER_MICROSECOND, 1)
            handle.push_gpu_device_name(device_name)
            handle.push_monotonic_ns(end_time_us * NANOS_PER_MICROSECOND)
            handle.push_threadinfo(
                e.thread, _threading.get_thread_native_id(e.thread), _threading.get_thread_name(e.thread)
            )
            handle.push_frame(e.name, "", 0, -1)
            handle.flush_sample()

        if e.flops is not None and e.flops > 0:
            # gpu flops sample
            handle = ddup.SampleHandle()
            handle.push_gpu_flops(e.flops, 1)
            handle.push_gpu_device_name(device_name)
            handle.push_frame(e.name, "", 0, -1)
            handle.flush_sample()

        if e.flops is not None and e.cuda_memory_usage > 0:
            # gpu mem sample
            handle = ddup.SampleHandle()
            handle.push_gpu_mem(e.cuda_memory_usage, 1)
            handle.push_gpu_device_name(device_name)
            handle.push_frame(e.name, "", 0, -1)
            handle.flush_sample()
