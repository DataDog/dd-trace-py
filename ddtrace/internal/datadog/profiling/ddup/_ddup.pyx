# distutils: language = c++
# cython: language_level=3

import platform
import typing
from typing import Optional

import ddtrace
from ddtrace.internal import runtime
from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace._trace.span import Span

from .utils import sanitize_string


IF UNAME_SYSNAME == "Linux":

    cdef extern from "<string_view>" namespace "std" nogil:
        cdef cppclass string_view:
            string_view(const char* s, size_t count)

    cdef pystr_to_sv(object s):
        cdef bytes s_bytes = b""
        try:
            s_bytes = ensure_binary(s)
        except Exception:
            pass
        return string_view(<const char*>s_bytes, len(s_bytes))

    cdef extern from "types.hpp":
        ctypedef enum ProfileType "ProfileType":
            CPU         "ProfileType::CPU"
            Wall        "ProfileType::Wall"
            Exception   "ProfileType::Exception"
            LockAcquire "ProfileType::LockAcquire"
            LockRelease "ProfileType::LockRelease"
            Allocation  "ProfileType::Allocation"
            Heap        "ProfileType::Heap"
            All         "ProfileType::All"

    cdef extern from "sample.hpp" namespace "Datadog":
        ctypedef struct Sample:
            pass

    cdef extern from "interface.hpp":
        ctypedef signed int int64_t
        ctypedef unsigned int uint64_t
        void ddup_config_env(string_view env)
        void ddup_config_service(string_view service)
        void ddup_config_version(string_view version)
        void ddup_config_runtime(string_view runtime)
        void ddup_config_runtime_version(string_view runtime_version)
        void ddup_config_profiler_version(string_view profiler_version)
        void ddup_config_url(string_view url)
        void ddup_config_max_nframes(int max_nframes)

        void ddup_config_user_tag(string_view key, string_view val)
        void ddup_config_sample_type(unsigned int type)

        void ddup_init()

        Sample *ddup_start_sample()
        void ddup_push_walltime(Sample *sample, int64_t walltime, int64_t count)
        void ddup_push_cputime(Sample *sample, int64_t cputime, int64_t count)
        void ddup_push_acquire(Sample *sample, int64_t acquire_time, int64_t count)
        void ddup_push_release(Sample *sample, int64_t release_time, int64_t count)
        void ddup_push_alloc(Sample *sample, uint64_t size, uint64_t count)
        void ddup_push_heap(Sample *sample, uint64_t size)
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
        void ddup_flush_sample(Sample *sample)
        void ddup_drop_sample(Sample *sample)
        void ddup_set_runtime_id(string_view _id)
        bint ddup_upload() nogil

    def init(
            service: Optional[str],
            env: Optional[str],
            version: Optional[str],
            tags: Optional[typing.Dict[str, str]],
            max_nframes: Optional[int],
            url: Optional[str]) -> None:

        # Try to provide a ddtrace-specific default service if one is not given
        service = service or DEFAULT_SERVICE_NAME
        ddup_config_service(pystr_to_sv(service))

        # If otherwise no values are provided, the uploader will omit the fields
        # and they will be auto-populated in the backend
        if env:
            ddup_config_env(pystr_to_sv(env))
        if version:
            ddup_config_version(pystr_to_sv(version))
        if url:
            ddup_config_url(pystr_to_sv(url))

        # Inherited
        ddup_config_runtime(pystr_to_sv(platform.python_implementation()))
        ddup_config_runtime_version(pystr_to_sv(platform.python_version()))
        ddup_config_profiler_version(pystr_to_sv(ddtrace.__version__))
        ddup_config_max_nframes(max_nframes)
        if tags is not None:
            for key, val in tags.items():
                if key and val:
                    ddup_config_user_tag(pystr_to_sv(key), pystr_to_sv(val))
        ddup_init()

    def upload() -> None:
        runtime_id = pystr_to_sv(runtime.get_runtime_id())
        ddup_set_runtime_id(runtime_id)
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
                ddup_push_cputime(self.ptr, value, count)

        def push_walltime(self, value: int, count: int) -> None:
            if self.ptr is not NULL:
                ddup_push_walltime(self.ptr, value, count)

        def push_acquire(self, value: int, count: int) -> None:
            if self.ptr is not NULL:
                ddup_push_acquire(self.ptr, value, count)

        def push_release(self, value: int, count: int) -> None:
            if self.ptr is not NULL:
                ddup_push_release(self.ptr, value, count)

        def push_alloc(self, value: int, count: int) -> None:
            if self.ptr is not NULL:
                ddup_push_alloc(self.ptr, value, count)

        def push_heap(self, value: int) -> None:
            if self.ptr is not NULL:
                ddup_push_heap(self.ptr, value)

        def push_lock_name(self, lock_name: str) -> None:
            if self.ptr is not NULL:
                ddup_push_lock_name(self.ptr, pystr_to_sv(lock_name))

        def push_frame(self, name: str, filename: str, int address, int line) -> None:
            if self.ptr is not NULL:
                name = sanitize_string(name)
                filename = sanitize_string(filename)
                ddup_push_frame(self.ptr, pystr_to_sv(name), pystr_to_sv(filename), address, line)

        def push_threadinfo(self, thread_id: int, thread_native_id: int, thread_name: str) -> None:
            if self.ptr is not NULL:
                thread_id = thread_id if thread_id is not None else 0
                thread_native_id = thread_native_id if thread_native_id is not None else 0
                thread_name = thread_name if thread_name is not None else ""
                ddup_push_threadinfo(self.ptr, thread_id, thread_native_id, pystr_to_sv(thread_name))

        def push_task_id(self, task_id: int) -> None:
            if self.ptr is not NULL:
                ddup_push_task_id(self.ptr, task_id)

        def push_task_name(self, task_name: str) -> None:
            if self.ptr is not NULL:
                if task_name:
                    ddup_push_task_name(self.ptr, pystr_to_sv(task_name))

        def push_exceptioninfo(self, exc_type: type, count: int) -> None:
            if self.ptr is not NULL:
                if exc_type is not None:
                    exc_name = exc_type.__module__ + "." + exc_type.__name__
                    ddup_push_exceptioninfo(self.ptr, pystr_to_sv(exc_name), count)

        def push_class_name(self, class_name: str) -> None:
            if self.ptr is not NULL:
                class_name = class_name if class_name is not None else ""
                ddup_push_class_name(self.ptr, pystr_to_sv(class_name))

        def push_span(self, span: typing.Optional[Span], endpoint_collection_enabled: bool) -> None:
            if self.ptr is NULL:
                return
            if not span:
                return
            if span.span_id:
                ddup_push_span_id(self.ptr, span.span_id)
            if not span._local_root:
                return
            if span._local_root.span_id:
                ddup_push_local_root_span_id(self.ptr, span._local_root.span_id)
            if span._local_root.span_type:
                ddup_push_trace_type(self.ptr, pystr_to_sv(span._local_root.span_type))
            if endpoint_collection_enabled:
                ddup_push_trace_resource_container(self.ptr, pystr_to_sv(span._local_root.service))

        def flush_sample(self) -> None:
            # Flushing the sample consumes it.  The user will no longer be able to use
            # this handle after flushing it.
            if self.ptr is not NULL:
                ddup_flush_sample(self.ptr)
                ddup_drop_sample(self.ptr)
                self.ptr = NULL
