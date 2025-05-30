# distutils: language = c++
# cython: language_level=3

import platform
from typing import Dict
from typing import Optional
from typing import Union

from cpython.unicode cimport PyUnicode_AsUTF8AndSize
from libcpp.memory cimport make_unique
from libcpp.memory cimport unique_ptr
from libcpp.unordered_map cimport unordered_map
from libcpp.utility cimport move
from libcpp.utility cimport pair

import ddtrace
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.datadog.profiling._types import StringType
from ddtrace.internal.datadog.profiling.code_provenance import json_str_to_export
from ddtrace.internal.datadog.profiling.util import sanitize_string
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.settings._agent import config as agent_config


ctypedef void (*func_ptr_t)(string_view)

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

cdef extern from "uploader_builder.hpp" namespace "Datadog":
    cdef cppclass UploaderConfig:
        UploaderConfig() except +
        void set_env(string_view _dd_env)
        void set_service(string_view _service)
        void set_version(string_view _version)
        void set_runtime(string_view _runtime)
        void set_runtime_id(string_view _runtime_id)
        void set_runtime_version(string_view _runtime_version)
        void set_profiler_version(string_view _profiler_version)
        void set_url(string_view _url)
        void set_tag(string_view _key, string_view _val)
        void set_output_filename(string_view _output_filename)

cdef extern from "ddup_interface.hpp":
    void ddup_config_max_nframes(int max_nframes)
    void ddup_config_timeline(bint enable)
    void ddup_config_sample_pool_capacity(uint64_t sample_pool_capacity)
    void ddup_config_sample_type(unsigned int type)

    void ddup_start()
    void ddup_set_runtime_id(string_view _id)
    void ddup_profile_set_endpoints(unordered_map[int64_t, string_view] span_ids_to_endpoints)
    void ddup_profile_add_endpoint_counts(unordered_map[string_view, int64_t] trace_endpoints_to_counts)
    bint ddup_upload(unique_ptr[UploaderConfig] config) nogil

    Sample *ddup_start_sample()
    void ddup_push_walltime(Sample *sample, int64_t walltime, int64_t count)
    void ddup_push_cputime(Sample *sample, int64_t cputime, int64_t count)
    void ddup_push_acquire(Sample *sample, int64_t acquire_time, int64_t count)
    void ddup_push_release(Sample *sample, int64_t release_time, int64_t count)
    void ddup_push_alloc(Sample *sample, int64_t size, int64_t count)
    void ddup_push_heap(Sample *sample, int64_t size)
    void ddup_push_gpu_gputime(Sample *sample, int64_t gputime, int64_t count)
    void ddup_push_gpu_memory(Sample *sample, int64_t size, int64_t count)
    void ddup_push_gpu_flops(Sample *sample, int64_t flops, int64_t count)
    void ddup_push_lock_name(Sample *sample, string_view lock_name)
    void ddup_push_threadinfo(Sample *sample, int64_t thread_id, int64_t thread_native_id, string_view thread_name)
    void ddup_push_task_id(Sample *sample, int64_t task_id)
    void ddup_push_task_name(Sample *sample, string_view task_name)
    void ddup_push_span_id(Sample *sample, uint64_t span_id)
    void ddup_push_local_root_span_id(Sample *sample, uint64_t local_root_span_id)
    void ddup_push_trace_type(Sample *sample, string_view trace_type)
    void ddup_push_exceptioninfo(Sample *sample, string_view exception_type, int64_t count)
    void ddup_push_class_name(Sample *sample, string_view class_name)
    void ddup_push_gpu_device_name(Sample *sample, string_view device_name)
    void ddup_push_frame(Sample *sample, string_view _name, string_view _filename, uint64_t address, int64_t line)
    void ddup_push_monotonic_ns(Sample *sample, int64_t monotonic_ns)
    void ddup_push_absolute_ns(Sample *sample, int64_t monotonic_ns)
    void ddup_flush_sample(Sample *sample)
    void ddup_drop_sample(Sample *sample)


cdef extern from "code_provenance_interface.hpp":
    void code_provenance_set_json_str(string_view json_str)

cdef call_code_provenance_set_json_str(str json_str):
    cdef const char* json_str_data
    cdef Py_ssize_t json_str_size
    json_str_data = PyUnicode_AsUTF8AndSize(json_str, &json_str_size)
    if json_str_data != NULL:
        code_provenance_set_json_str(string_view(json_str_data, json_str_size))

cdef call_ddup_profile_set_endpoints(endpoint_to_span_ids):
    # We want to make sure that endpoint strings outlive the for loop below
    # and prevent them to be GC'ed. We do this by storing them in a list.
    # This is necessary because we pass string_views to the C++ code, which is
    # a view into the original string. If the original string is GC'ed, the view
    # will point to garbage.
    endpoint_list = []
    cdef unordered_map[int64_t, string_view] span_ids_to_endpoints = unordered_map[int64_t, string_view]()
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    for endpoint, span_ids in endpoint_to_span_ids.items():
        if not endpoint:
            continue
        endpoint_list.append(endpoint)
        if isinstance(endpoint, bytes):
            for span_id in span_ids:
                span_ids_to_endpoints.insert(
                    pair[int64_t, string_view](
                        clamp_to_uint64_unsigned(span_id),
                        string_view(<const char*>endpoint, len(endpoint))
                    )
                )
            continue
        utf8_data = PyUnicode_AsUTF8AndSize(endpoint, &utf8_size)
        if utf8_data != NULL:
            for span_id in span_ids:
                span_ids_to_endpoints.insert(
                    pair[int64_t, string_view](
                        clamp_to_uint64_unsigned(span_id),
                        string_view(utf8_data, utf8_size)
                    )
                )
    ddup_profile_set_endpoints(span_ids_to_endpoints)

cdef call_ddup_profile_add_endpoint_counts(endpoint_counts):
    # We want to make sure that endpoint strings outlive the for loop below
    # and prevent them to be GC'ed. We do this by storing them in a list.
    # This is necessary because we pass string_views to the C++ code, which is
    # a view into the original string. If the original string is GC'ed, the view
    # will point to garbage.
    endpoint_list = []
    cdef unordered_map[string_view, int64_t] trace_endpoints_to_counts = unordered_map[string_view, int64_t]()
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    for endpoint, count in endpoint_counts.items():
        if not endpoint:
            continue
        endpoint_list.append(endpoint)
        if isinstance(endpoint, bytes):
            trace_endpoints_to_counts.insert(
                pair[string_view, int64_t](
                    string_view(<const char*>endpoint, len(endpoint)),
                    clamp_to_int64_unsigned(count)
                )
            )
            continue
        utf8_data = PyUnicode_AsUTF8AndSize(endpoint, &utf8_size)
        if utf8_data != NULL:
            trace_endpoints_to_counts.insert(
                pair[string_view, int64_t](
                    string_view(utf8_data, utf8_size),
                    clamp_to_int64_unsigned(count)
                )
            )
    ddup_profile_add_endpoint_counts(trace_endpoints_to_counts)

cdef call_ddup_push_lock_name(Sample* sample, lock_name: StringType):
    if not lock_name:
        return
    if isinstance(lock_name, bytes):
        ddup_push_lock_name(sample, string_view(<const char*>lock_name, len(lock_name)))
        return
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    utf8_data = PyUnicode_AsUTF8AndSize(lock_name, &utf8_size)
    if utf8_data != NULL:
        ddup_push_lock_name(sample, string_view(utf8_data, utf8_size))

cdef call_ddup_push_frame(Sample* sample, name: StringType, filename: StringType,
                          uint64_t address, int64_t line):
    if not name or not filename:
        return
    if isinstance(name, bytes) and isinstance(filename, bytes):
        ddup_push_frame(sample, string_view(<const char*>name, len(name)),
                        string_view(<const char*>filename, len(filename)),
                        address, line)
        return
    cdef const char* name_utf8_data
    cdef Py_ssize_t name_utf8_size
    cdef const char* filename_utf8_data
    cdef Py_ssize_t filename_utf8_size
    name_utf8_data = PyUnicode_AsUTF8AndSize(name, &name_utf8_size)
    filename_utf8_data = PyUnicode_AsUTF8AndSize(filename, &filename_utf8_size)
    if name_utf8_data != NULL and filename_utf8_data != NULL:
        ddup_push_frame(sample, string_view(name_utf8_data, name_utf8_size),
                        string_view(filename_utf8_data, filename_utf8_size),
                        address, line)

cdef call_ddup_push_threadinfo(Sample* sample, int64_t thread_id, int64_t thread_native_id, thread_name: StringType):
    if not thread_name:
        return
    if isinstance(thread_name, bytes):
        ddup_push_threadinfo(
            sample, thread_id, thread_native_id, string_view(<const char*>thread_name, len(thread_name)))
        return
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    utf8_data = PyUnicode_AsUTF8AndSize(thread_name, &utf8_size)
    if utf8_data != NULL:
        ddup_push_threadinfo(sample, thread_id, thread_native_id, string_view(utf8_data, utf8_size))

cdef call_ddup_push_task_name(Sample* sample, task_name: StringType):
    if not task_name:
        return
    if isinstance(task_name, bytes):
        ddup_push_task_name(sample, string_view(<const char*>task_name, len(task_name)))
        return
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    utf8_data = PyUnicode_AsUTF8AndSize(task_name, &utf8_size)
    if utf8_data != NULL:
        ddup_push_task_name(sample, string_view(utf8_data, utf8_size))

cdef call_ddup_push_exceptioninfo(Sample* sample, exception_name: StringType, uint64_t count):
    if not exception_name:
        return
    if isinstance(exception_name, bytes):
        ddup_push_exceptioninfo(sample, string_view(<const char*>exception_name, len(exception_name)), count)
        return
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    utf8_data = PyUnicode_AsUTF8AndSize(exception_name, &utf8_size)
    if utf8_data != NULL:
        ddup_push_exceptioninfo(sample, string_view(utf8_data, utf8_size), count)

cdef call_ddup_push_class_name(Sample* sample, class_name: StringType):
    if not class_name:
        return
    if isinstance(class_name, bytes):
        ddup_push_class_name(sample, string_view(<const char*>class_name, len(class_name)))
        return
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    utf8_data = PyUnicode_AsUTF8AndSize(class_name, &utf8_size)
    if utf8_data != NULL:
        ddup_push_class_name(sample, string_view(utf8_data, utf8_size))

cdef call_ddup_push_gpu_device_name(Sample* sample, device_name: StringType):
    if not device_name:
        return
    if isinstance(device_name, bytes):
        ddup_push_gpu_device_name(sample, string_view(<const char*>device_name, len(device_name)))
        return
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    utf8_data = PyUnicode_AsUTF8AndSize(device_name, &utf8_size)
    if utf8_data != NULL:
        ddup_push_gpu_device_name(sample, string_view(utf8_data, utf8_size))

cdef call_ddup_push_trace_type(Sample* sample, trace_type: StringType):
    if not trace_type:
        return
    if isinstance(trace_type, bytes):
        ddup_push_trace_type(sample, string_view(<const char*>trace_type, len(trace_type)))
        return
    cdef const char* utf8_data
    cdef Py_ssize_t utf8_size
    utf8_data = PyUnicode_AsUTF8AndSize(trace_type, &utf8_size)
    if utf8_data != NULL:
        ddup_push_trace_type(sample, string_view(utf8_data, utf8_size))

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


# Module-level flag to track if code provenance has been set
cdef bint _code_provenance_set = False


def config(
        max_nframes: Optional[int] = None,
        timeline_enabled: Optional[bool] = None,
        sample_pool_capacity: Optional[int] = None) -> None:
    if max_nframes is not None:
        ddup_config_max_nframes(clamp_to_int64_unsigned(max_nframes))
    if timeline_enabled is True:
        ddup_config_timeline(True)
    if sample_pool_capacity:
        ddup_config_sample_pool_capacity(clamp_to_uint64_unsigned(sample_pool_capacity))


def start() -> None:
    ddup_start()


def _get_endpoint(tracer)-> str:
    # DEV: ddtrace.profiling.utils has _get_endpoint but importing that function
    # leads to a circular import, so re-implementing it here.
    # TODO(taegyunkim): support agentless mode by modifying uploader_builder to
    # build exporter for agentless mode too.
    tracer_agent_url = tracer.agent_trace_url
    endpoint = tracer_agent_url if tracer_agent_url else agent_config.trace_agent_url
    return endpoint


def upload(
    tracer: Optional[Tracer] = ddtrace.tracer,
    enable_code_provenance: Optional[bool] = None,
    service: StringType = None,
    env: StringType = None,
    version: StringType = None,
    tags: Optional[Dict[str, str]] = None,
    output_filename: StringType = None,
) -> None:
    global _code_provenance_set

    cdef unique_ptr[UploaderConfig] config = make_unique[UploaderConfig]()
    cdef Py_ssize_t c_str_len
    cdef const char* c_str

    # Note these set_* functions allocate a new string on C++ side, so we only
    # need to call these with string_view to avoid extra copies.
    service = service or DEFAULT_SERVICE_NAME
    c_str = PyUnicode_AsUTF8AndSize(service, &c_str_len)
    if c_str != NULL:
        config.get().set_service(string_view(c_str, c_str_len))

    if env:
        c_str = PyUnicode_AsUTF8AndSize(env, &c_str_len)
        if c_str != NULL:
            config.get().set_env(string_view(c_str, c_str_len))

    if version:
        c_str = PyUnicode_AsUTF8AndSize(version, &c_str_len)
        if c_str != NULL:
            config.get().set_version(string_view(c_str, c_str_len))

    cdef Py_ssize_t val_len
    cdef const char* val_ptr

    if tags:
        for key, val in tags.items():
            if key and val:
                c_str = PyUnicode_AsUTF8AndSize(key, &c_str_len)
                val_ptr = PyUnicode_AsUTF8AndSize(val, &val_len)
                if c_str != NULL and val_ptr != NULL:
                    config.get().set_tag(string_view(c_str, c_str_len), string_view(val_ptr, val_len))

    if output_filename:
        c_str = PyUnicode_AsUTF8AndSize(output_filename, &c_str_len)
        if c_str != NULL:
            config.get().set_output_filename(string_view(c_str, c_str_len))

    runtime = platform.python_implementation()
    if runtime:
        c_str = PyUnicode_AsUTF8AndSize(runtime, &c_str_len)
        if c_str != NULL:
            config.get().set_runtime(string_view(c_str, c_str_len))

    runtime_id = get_runtime_id()
    if runtime_id:
        c_str = PyUnicode_AsUTF8AndSize(runtime_id, &c_str_len)
        if c_str != NULL:
            config.get().set_runtime_id(string_view(c_str, c_str_len))

    runtime_version = platform.python_version()
    if runtime_version:
        c_str = PyUnicode_AsUTF8AndSize(runtime_version, &c_str_len)
        if c_str != NULL:
            config.get().set_runtime_version(string_view(c_str, c_str_len))

    endpoint = _get_endpoint(tracer)
    if endpoint:
        c_str = PyUnicode_AsUTF8AndSize(endpoint, &c_str_len)
        if c_str != NULL:
            config.get().set_url(string_view(c_str, c_str_len))

    profiler_version = ddtrace.__version__
    if profiler_version:
        c_str = PyUnicode_AsUTF8AndSize(profiler_version, &c_str_len)
        if c_str != NULL:
            config.get().set_profiler_version(string_view(c_str, c_str_len))

    processor = tracer._endpoint_call_counter_span_processor
    endpoint_counts, endpoint_to_span_ids = processor.reset()

    call_ddup_profile_set_endpoints(endpoint_to_span_ids)
    call_ddup_profile_add_endpoint_counts(endpoint_counts)

    if enable_code_provenance and not _code_provenance_set:
        call_code_provenance_set_json_str(json_str_to_export())
        _code_provenance_set = True

    with nogil:
        ddup_upload(move(config))


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

    def push_gpu_gputime(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_gpu_gputime(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_gpu_memory(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_gpu_memory(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_gpu_flops(self, value: int, count: int) -> None:
        if self.ptr is not NULL:
            ddup_push_gpu_flops(self.ptr, clamp_to_int64_unsigned(value), clamp_to_int64_unsigned(count))

    def push_lock_name(self, lock_name: StringType) -> None:
        if self.ptr is not NULL:
            call_ddup_push_lock_name(self.ptr, lock_name)

    def push_frame(self, name: StringType, filename: StringType, address: int, line: int) -> None:
        if self.ptr is not NULL:
            # Customers report `name` and `filename` may be unexpected objects, so sanitize.
            sanitized_name = sanitize_string(name)
            sanitized_filename = sanitize_string(filename)
            call_ddup_push_frame(self.ptr, sanitized_name, sanitized_filename,
                                 clamp_to_uint64_unsigned(address), clamp_to_int64_unsigned(line))

    def push_threadinfo(self, thread_id: int, thread_native_id: int, thread_name: StringType) -> None:
        if self.ptr is not NULL:
            thread_id = thread_id if thread_id is not None else 0
            thread_native_id = thread_native_id if thread_native_id is not None else 0
            call_ddup_push_threadinfo(
                self.ptr,
                clamp_to_int64_unsigned(thread_id),
                clamp_to_int64_unsigned(thread_native_id),
                thread_name
            )

    def push_task_id(self, task_id: Optional[int]) -> None:
        if self.ptr is not NULL:
            if task_id is not None:
                ddup_push_task_id(self.ptr, clamp_to_int64_unsigned(task_id))

    def push_task_name(self, task_name: StringType) -> None:
        if self.ptr is not NULL:
            if task_name is not None:
                call_ddup_push_task_name(self.ptr, task_name)

    def push_exceptioninfo(self, exc_type: Union[None, bytes, str, type], count: int) -> None:
        if self.ptr is not NULL:
            exc_name = None
            if isinstance(exc_type, type):
                exc_name = exc_type.__module__ + "." + exc_type.__name__
            else:
                exc_name = exc_type
            call_ddup_push_exceptioninfo(self.ptr, exc_name, clamp_to_uint64_unsigned(count))

    def push_class_name(self, class_name: StringType) -> None:
        if self.ptr is not NULL:
            call_ddup_push_class_name(self.ptr, class_name)

    def push_gpu_device_name(self, device_name: StringType) -> None:
        if self.ptr is not NULL:
            call_ddup_push_gpu_device_name(self.ptr, device_name)

    def push_span(self, span: Optional[Span]) -> None:
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
            call_ddup_push_trace_type(self.ptr, span._local_root.span_type)

    def push_monotonic_ns(self, monotonic_ns: int) -> None:
        if self.ptr is not NULL:
            ddup_push_monotonic_ns(self.ptr, <int64_t>monotonic_ns)

    def push_absolute_ns(self, timestamp_ns: int) -> None:
        if self.ptr is not NULL:
            ddup_push_absolute_ns(self.ptr, <int64_t>timestamp_ns)

    def flush_sample(self) -> None:
        # Flushing the sample consumes it.  The user will no longer be able to use
        # this handle after flushing it.
        if self.ptr is not NULL:
            ddup_flush_sample(self.ptr)
            ddup_drop_sample(self.ptr)
            self.ptr = NULL
