# -*- encoding: utf-8 -*-
import logging
from math import ceil
import os
import threading
import typing  # noqa:F401
import socket
import json

import attr


from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.settings.profiling import config


LOG = logging.getLogger(__name__)


@attr.s
class CudaEventCollector(collector.PeriodicCollector):
    """Memory allocation collector."""

    _DEFAULT_MAX_EVENTS = 100
    _DEFAULT_INTERVAL = 0.5

    # Arbitrary interval to empty the event buffer
    _interval = attr.ib(default=_DEFAULT_INTERVAL, repr=False)

    _max_events = attr.ib(type=int, default=config.memory.events_buffer)
    max_nframe = attr.ib(default=config.max_frames, type=int)
    heap_sample_size = attr.ib(type=int, default=config.heap.sample_size)
    ignore_profiler = attr.ib(default=config.ignore_profiler, type=bool)
    _export_libdd_enabled = attr.ib(type=bool, default=config.export.libdd_enabled)
    _export_py_enabled = attr.ib(type=bool, default=config.export.py_enabled)

    # Set the path for the Unix socket
    socket_path = "/tmp/my_socket"

    # Create the Unix socket client
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def _start_service(self):
        # type: (...) -> None
        """Start collecting cuda event profiles."""
        # Connect to the server
        self.client.connect(self.socket_path)
        # Send a message to the server
        message = "Hello from the client, test!"
        self.client.sendall(message.encode())

        super(CudaEventCollector, self)._start_service()

    def on_shutdown(self):
        # type: () -> None
        self.client.close()

    def _get_thread_id_ignore_set(self):
        # type: () -> typing.Set[int]
        # This method is not perfect and prone to race condition in theory, but very little in practice.
        # Anyhow it's not a big deal — it's a best effort feature.
        return {
            thread.ident
            for thread in threading.enumerate()
            if getattr(thread, "_ddtrace_profiling_ignore", False) and thread.ident is not None
        }

    def snapshot(self):
        print("TESTING - HIT SNAPSHOT")
        thread_id_ignore_set = self._get_thread_id_ignore_set()
        events = []
        try:
            # Receive a response from the server
            response = self.client.recv(1024 * 1000)
            decoded_response = response.decode()
            first_match = decoded_response.index("{")
            last_match = decoded_response.rindex("}")
            decoded_response_truncated = decoded_response[first_match : last_match + 1]
            LOG.debug(decoded_response_truncated)
            events_json = decoded_response_truncated.strip(" ").split("}")
            for e in events_json:
                if e == "Hello from the server!" or len(e) <= 0:
                    continue
                event = e + "}"
                events.append(json.loads(event))
            LOG.debug("TESTING SEE EVENTS")
            if len(events) > 0:
                LOG.debug(events[0])
        except RuntimeError:
            LOG.debug("Unable to collect cuda ebpf events from process %d", os.getpid(), exc_info=True)
            return tuple()

        if self._export_libdd_enabled:
            for e in events:
                thread_id = e.get("tid", -1)
                if not self.ignore_profiler or thread_id not in thread_id_ignore_set:
                    nframes = 1  # for now we don't capture frames
                    ddup.start_sample(nframes)
                    ddup.push_threadinfo(
                        thread_id, _threading.get_thread_native_id(thread_id), _threading.get_thread_name(thread_id)
                    )
                    ts = e.get("ts", 0)
                    try:
                        # for frame in frames:
                        #    ddup.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                        ddup.push_cuda_event(1)
                        ddup.push_end_timestamp_ns(ts)
                        ddup.push_cuda_event_type("cudaLaunchKernel")
                        ddup.push_frame("cudaLaunchKernel", "", 0, -1)
                        ddup.flush_sample()
                    except AttributeError:
                        LOG.debug("Invalid state detected in cuda events module, suppressing profile")

        else:
            return tuple()

    def collect(self):
        print("TESTING - CALLED INTO COLLECT")
        thread_id_ignore_set = self._get_thread_id_ignore_set()
        events = []
        try:
            # Receive a response from the server
            response = self.client.recv(1024 * 1000)
            decoded_response = response.decode()
            first_match = decoded_response.index("{")
            last_match = decoded_response.rindex("}")
            decoded_response_truncated = decoded_response[first_match : last_match + 1]
            LOG.debug(decoded_response_truncated)
            events_json = decoded_response_truncated.strip(" ").split("}")
            for e in events_json:
                if e == "Hello from the server!" or len(e) <= 0:
                    continue
                event = e + "}"
                events.append(json.loads(event))
            LOG.debug("TESTING SEE EVENTS")
            if len(events) > 0:
                LOG.debug(events[0])
        except RuntimeError:
            LOG.debug("Unable to collect cuda ebpf events from process %d", os.getpid(), exc_info=True)
            return tuple()

        if self._export_libdd_enabled:
            for e in events:
                thread_id = e.get("thread_id", -1)
                if not self.ignore_profiler or thread_id not in thread_id_ignore_set:
                    nframes = 1  # for now we don't capture frames
                    ddup.start_sample(nframes)
                    ddup.push_threadinfo(
                        thread_id, _threading.get_thread_native_id(thread_id), _threading.get_thread_name(thread_id)
                    )
                    try:
                        # for frame in frames:
                        #    ddup.push_frame(frame.function_name, frame.file_name, 0, frame.lineno)
                        ddup.push_frame("cudaLaunchKernel", "", 0, -1)
                        ddup.flush_sample()
                    except AttributeError:
                        LOG.debug("Invalid state detected in cuda events module, suppressing profile")

        else:
            return []
