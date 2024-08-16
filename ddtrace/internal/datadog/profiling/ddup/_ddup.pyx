# distutils: language = c++
# cython: language_level=3

from typing import Dict
from typing import Optional
from typing import Union

import ddtrace
import platform
from ..types import StringType
from ..util import ensure_binary_or_empty
from ..util import sanitize_string
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.runtime import get_runtime_id
from ddtrace._trace.span import Span


cdef extern from "stdint.h":
    ctypedef unsigned long long uint64_t
    ctypedef long long int64_t
    cdef uint64_t UINT64_MAX
    cdef int64_t INT64_MAX

cdef extern from "<string_view>" namespace "std" nogil:
    cdef cppclass string_view:
        string_view(const char* s, size_t count)

cdef extern from "sample.hpp" namespace "Datadog":
    ctypedef struct Sample:
        pass

cdef extern from "ddup_interface.hpp":
    void ddup_config_env(string_view env)
    void ddup_config_service(string_view service)
    void ddup_config_version(string_view version)
    void ddup_config_runtime(string_view runtime)
    void ddup_config_runtime_version(string_view runtime_version)
    void ddup_config_profiler_version(string_view profiler_version)
    void ddup_config_url(string_view url)
    void ddup_config_max_nframes(int max_nframes)
    void ddup_config_timeline(bint enable)

    void ddup_config_user_tag(string_view key, string_view val)
    void ddup_config_sample_type(unsigned int type)

    void ddup_start()

    Sample *ddup_start_sample()
    void ddup_push_walltime(Sample *sample, int64_t walltime, int64_t count)
    void ddup_push_cputime(Sample *sample, int64_t cputime, int64_t count)
    void ddup_push_acquire(Sample *sample, int64_t acquire_time, int64_t count)
    void ddup_push_release(Sample *sample, int64_t release_time, int64_t count)
    void ddup_push_alloc(Sample *sample, int64_t size, int64_t count)
    void ddup_push_heap(Sample *sample, int64_t size)
    void ddup_push_lock_name(Sample *sample, string_view lock_name)
    void ddup_push_threadinfo(Sample *sample, int64_t thread_id, int64_t thread_native_id, string_view thread_name)
    void ddup_push_task_id(Sample *sample, int64_t task_id)
    void ddup_push_task_name(Sample *sample, string_view task_name)
    void ddup_push_span_id(Sample *sample, uint64_t span_id)
    void ddup_push_local_root_span_id(Sample *sample, uint64_t local_root_span_id)
    void ddup_push_trace_type(Sample *sample, string_view trace_type)
    void ddup_push_trace_resource_container(Sample *sample, string_view trace_resource_container)
    void ddup_push_exceptioninfo(Sample *sample, string_view exception_type, int64_t count)
    void ddup_push_class_name(Sample *sample, string_view class_name)
    void ddup_push_frame(Sample *sample, string_view _name, string_view _filename, uint64_t address, int64_t line)
    void ddup_push_monotonic_ns(Sample *sample, int64_t monotonic_ns)
    void ddup_flush_sample(Sample *sample)
    void ddup_drop_sample(Sample *sample)
    void ddup_set_runtime_id(string_view _id)
    bint ddup_upload() nogil

# Create wrappers for cython
cdef call_ddup_config_service(bytes service):
    ddup_config_service(string_view(<const char*>service, len(service)))

cdef call_ddup_config_env(bytes env):
    ddup_config_env(string_view(<const char*>env, len(env)))

cdef call_ddup_config_version(bytes version):
    ddup_config_version(string_view(<const char*>version, len(version)))

cdef call_ddup_config_url(bytes url):
    ddup_config_url(string_view(<const char*>url, len(url)))

cdef call_ddup_config_runtime(bytes runtime):
    ddup_config_runtime(string_view(<const char*>runtime, len(runtime)))

cdef call_ddup_config_runtime_version(bytes runtime_version):
    ddup_config_runtime_version(string_view(<const char*>runtime_version, len(runtime_version)))

cdef call_ddup_config_profiler_version(bytes profiler_version):
    ddup_config_profiler_version(string_view(<const char*>profiler_version, len(profiler_version)))

cdef call_ddup_config_user_tag(bytes key, bytes val):
    ddup_config_user_tag(string_view(<const char*>key, len(key)), string_view(<const char*>val, len(val)))


# Conversion functions
cdef uint64_t clamp_to_uint64_unsigned(value):
    # This clamps a Python int to the nonnegative range of an unsigned 64-bit integer.
    # The name is redundant, but consistent with the other clamping function.
    if value < 0:
        return 0
    if value > UINT64_MAX:
        return UINT64_MAX
    return value


cdef int64_t clamp_to_int64_unsigned(value):
    # This clamps a Python int to the nonnegative range of a signed 64-bit integer.
    if value < 0:
        return 0
    if value > INT64_MAX:
        return INT64_MAX
    return value


# Public API
def config(
        service: StringType = None,
        env: StringType = None,
        version: StringType = None,
        tags: Optional[Dict[Union[str, bytes], Union[str, bytes]]] = None,
        max_nframes: Optional[int] = None,
        url: StringType = None,
        timeline_enabled: Optional[bool] = None) -> None:

    # Try to provide a ddtrace-specific default service if one is not given
    service = service or DEFAULT_SERVICE_NAME
    call_ddup_config_service(ensure_binary_or_empty(service))

    # Empty values are auto-populated in the backend (omitted in client)
    if env:
        call_ddup_config_env(ensure_binary_or_empty(env))
    if version:
        call_ddup_config_version(ensure_binary_or_empty(version))
    if url:
        call_ddup_config_url(ensure_binary_or_empty(url))

    # Inherited
    call_ddup_config_runtime(ensure_binary_or_empty(platform.python_implementation()))
    call_ddup_config_runtime_version(ensure_binary_or_empty(platform.python_version()))
    call_ddup_config_profiler_version(ensure_binary_or_empty(ddtrace.__version__))

    if max_nframes is not None:
        ddup_config_max_nframes(clamp_to_int64_unsigned(max_nframes))
    if tags is not None:
        for key, val in tags.items():
            if key and val:
                call_ddup_config_user_tag(ensure_binary_or_empty(key), ensure_binary_or_empty(val))

    if timeline_enabled is True:
        ddup_config_timeline(True)


def start() -> None:
    ddup_start()


def upload() -> None:
    runtime_id = ensure_binary_or_empty(get_runtime_id())
    ddup_set_runtime_id(string_view(<const char*>runtime_id, len(runtime_id)))
    with nogil:
        ddup_upload()


cdef class SampleHandle:
    cdef Sample *ptr

    def __cinit__(self):
        self.ptr = ddup_start_sample()

    def __dealloc__(self):
        if self.ptr is not NULL:
            ddup_drop_sample(self.ptr)
            self.ptr = NULL  # defensively, in case of post-dealloc access in native

    def push_cputime(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_cputime(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_walltime(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_walltime(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_acquire(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_acquire(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_release(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_release(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_alloc(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_alloc(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_heap(self, value: int) -> None:
        if self.ptr is not NULL:
            ddup_push_heap(self.ptr, clamp_to_int64_unsigned(value))

    def push_lock_name(self, lock_name: StringType) -> None:
        if self.ptr is not NULL:
            lock_name_bytes = ensure_binary_or_empty(lock_name)
            ddup_push_lock_name(self.ptr, string_view(<const char*>lock_name_bytes, len(lock_name_bytes)))

    def push_frame(self, name: StringType, filename: StringType, address: int, line: int) -> None:
        if self.ptr is not NULL:
            # Customers report `name` and `filename` may be unexpected objects, so sanitize.
            name_bytes = ensure_binary_or_empty(sanitize_string(name))
            filename_bytes = ensure_binary_or_empty(sanitize_string(filename))
            ddup_push_frame(
                    self.ptr,
                    string_view(<const char*>name_bytes, len(name_bytes)),
                    string_view(<const char*>filename_bytes, len(filename_bytes)),
                    clamp_to_uint64_unsigned(address),
                    clamp_to_int64_unsigned(line),
            )

    def push_threadinfo(self, thread_id: int, thread_native_id: int, thread_name: StringType) -> None:
        if self.ptr is not NULL:
            thread_id = thread_id if thread_id is not None else 0
            thread_native_id = thread_native_id if thread_native_id is not None else 0
            thread_name_bytes = ensure_binary_or_empty(thread_name)
            ddup_push_threadinfo(
                    self.ptr,
                    clamp_to_int64_unsigned(thread_id),
                    clamp_to_int64_unsigned(thread_native_id),
                    string_view(<const char*>thread_name_bytes, len(thread_name_bytes))
            )

    def push_task_id(self, task_id: Optional[int]) -> None:
        if self.ptr is not NULL:
            if task_id is not None:
                ddup_push_task_id(self.ptr, clamp_to_int64_unsigned(task_id))

    def push_task_name(self, task_name: StringType) -> None:
        if self.ptr is not NULL:
            if task_name is not None:
                task_name_bytes = ensure_binary_or_empty(task_name)
                ddup_push_task_name(self.ptr, string_view(<const char*>task_name_bytes, len(task_name_bytes)))

    def push_exceptioninfo(self, exc_type: Union[None, bytes, str, type], count: int) -> None:
        if self.ptr is not NULL:
            exc_name = None
            if isinstance(exc_type, type):
                exc_name = ensure_binary_or_empty(exc_type.__module__ + "." + exc_type.__name__)
            else:
                exc_name = ensure_binary_or_empty(exc_type)
            ddup_push_exceptioninfo(
                self.ptr,
                string_view(<const char*>exc_name, len(exc_name)),
                clamp_to_int64_unsigned(count)
            )

    def push_class_name(self, class_name: StringType) -> None:
        if self.ptr is not NULL:
            class_name_bytes = ensure_binary_or_empty(class_name)
            ddup_push_class_name(self.ptr, string_view(<const char*>class_name_bytes, len(class_name_bytes)))

    def push_span(self, span: Optional[Span], endpoint_collection_enabled: bool) -> None:
        if self.ptr is NULL:
            return
        if not span:
            return
        if span.span_id:
            ddup_push_span_id(self.ptr, clamp_to_uint64_unsigned(span.span_id))
        if not span._local_root:
            return
        if span._local_root.span_id:
            ddup_push_local_root_span_id(self.ptr, clamp_to_uint64_unsigned(span._local_root.span_id))
        if span._local_root.span_type:
            span_type_bytes = ensure_binary_or_empty(span._local_root.span_type)
            ddup_push_trace_type(self.ptr, string_view(<const char*>span_type_bytes, len(span_type_bytes)))
        if endpoint_collection_enabled:
            root_resource_bytes = ensure_binary_or_empty(span._local_root.resource)
            ddup_push_trace_resource_container(
                    self.ptr,
                    string_view(<const char*>root_resource_bytes, len(root_resource_bytes))
            )

    def push_monotonic_ns(self, monotonic_ns: int) -> None:
        if self.ptr is not NULL:
            ddup_push_monotonic_ns(self.ptr, <int64_t>monotonic_ns)

    def flush_sample(self) -> None:
        # Flushing the sample consumes it.  The user will no longer be able to use
        # this handle after flushing it.
        if self.ptr is not NULL:
            ddup_flush_sample(self.ptr)
            ddup_drop_sample(self.ptr)
            self.ptr = NULL
