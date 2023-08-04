import platform
import typing
from typing import Optional

import ddtrace
from ddtrace.internal import runtime
from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.span import Span


IF UNAME_SYSNAME == "Linux":
    cdef extern from "exporter.hpp":
        ctypedef enum ProfileType "ProfileType":
            CPU         "ProfileType::CPU"
            Wall        "ProfileType::Wall"
            Exception   "ProfileType::Exception"
            LockAcquire "ProfileType::LockAcquire"
            LockRelease "ProfileType::LockRelease"
            Allocation  "ProfileType::Allocation"
            Heap        "ProfileType::Heap"
            All         "ProfileType::All"
    cdef extern from "interface.hpp":
        ctypedef signed int int64_t
        ctypedef unsigned int uint64_t
        void ddup_config_env(const char *env)
        void ddup_config_service(const char *service)
        void ddup_config_version(const char *version)
        void ddup_config_runtime(const char *runtime)
        void ddup_config_runtime_version(const char *runtime_version)
        void ddup_config_profiler_version(const char *profiler_version)
        void ddup_config_url(const char *url)
        void ddup_config_max_nframes(int max_nframes)

        void ddup_config_user_tag(const char *key, const char *val)
        void ddup_config_sample_type(unsigned int type)

        void ddup_init()

        void ddup_start_sample(unsigned int nframes)
        void ddup_push_walltime(int64_t walltime, int64_t count)
        void ddup_push_cputime(int64_t cputime, int64_t count)
        void ddup_push_acquire(int64_t acquire_time, int64_t count)
        void ddup_push_release(int64_t release_time, int64_t count)
        void ddup_push_alloc(uint64_t size, uint64_t count)
        void ddup_push_heap(uint64_t size)
        void ddup_push_lock_name(const char *lock_name)
        void ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char *thread_name)
        void ddup_push_task_id(int64_t task_id)
        void ddup_push_task_name(const char *task_name)
        void ddup_push_span_id(int64_t span_id)
        void ddup_push_local_root_span_id(int64_t local_root_span_id)
        void ddup_push_trace_type(const char *trace_type)
        void ddup_push_trace_resource_container(const char *trace_resource_container)
        void ddup_push_exceptioninfo(const char *exception_type, int64_t count)
        void ddup_push_class_name(const char *class_name)
        void ddup_push_frame(const char *_name, const char *_filename, uint64_t address, int64_t line)
        void ddup_flush_sample()
        void ddup_set_runtime_id(const char *_id, size_t sz)
        void ddup_upload()

    def init(
            service: Optional[str],
            env: Optional[str],
            version: Optional[str],
            tags: Optional[typing.Dict[str, str]],
            max_nframes: Optional[int],
            url: Optional[str]) -> None:

        # Try to provide a ddtrace-specific default service if one is not given
        service = service or DEFAULT_SERVICE_NAME
        ddup_config_service(ensure_binary(service))

        # If otherwise no values are provided, the uploader will omit the fields
        # and they will be auto-populated in the backend
        if env:
            ddup_config_env(ensure_binary(env))
        if version:
            ddup_config_version(ensure_binary(version))
        if url:
            ddup_config_url(ensure_binary(url))

        # Inherited
        ddup_config_runtime(ensure_binary(platform.python_implementation()))
        ddup_config_runtime_version(ensure_binary(platform.python_version()))
        ddup_config_profiler_version(ensure_binary(ddtrace.__version__))
        ddup_config_max_nframes(max_nframes)
        if tags is not None:
            for key, val in tags.items():
                ddup_config_user_tag(key, val)
        ddup_init()

    def start_sample(nframes: int) -> None:
        ddup_start_sample(nframes)

    def push_cputime(value: int, count: int) -> None:
        ddup_push_cputime(value, count)

    def push_walltime(value: int, count: int) -> None:
        ddup_push_walltime(value, count)

    def push_acquire(value: int, count: int) -> None:
        ddup_push_acquire(value, count)

    def push_release(value: int, count: int) -> None:
        ddup_push_release(value, count)

    def push_alloc(value: int, count: int) -> None:
        ddup_push_alloc(value, count)

    def push_heap(value: int) -> None:
        ddup_push_heap(value)

    def push_lock_name(lock_name: str) -> None:
        ddup_push_lock_name(ensure_binary(lock_name))

    def push_frame(name: str, filename: str, address: int, line: int) -> None:
        name = name if name is not None else ""
        filename = filename if filename is not None else ""
        ddup_push_frame(ensure_binary(name), ensure_binary(filename), address, line)

    def push_threadinfo(thread_id: int, thread_native_id: int, thread_name: Optional[str]) -> None:
        # Type hints don't preclude the possibility of a None being propagated
        thread_id = thread_id if thread_id is not None else 0
        thread_native_id = thread_native_id if thread_native_id is not None else 0
        thread_name = thread_name if thread_name is not None else ""

        ddup_push_threadinfo(thread_id, thread_native_id, ensure_binary(thread_name))

    def push_task_id(task_id: int) -> None:
        ddup_push_task_id(task_id)

    def push_task_name(task_name: str) -> None:
        if task_name:
            ddup_push_task_name(ensure_binary(task_name))

    def push_exceptioninfo(exc_type: type, count: int) -> None:
        if exc_type is not None:
            exc_name = exc_type.__module__ + "." + exc_type.__name__
            ddup_push_exceptioninfo(ensure_binary(exc_name), count)

    def push_class_name(class_name: str) -> None:
        class_name = class_name if class_name is not None else ""
        ddup_push_class_name(ensure_binary(class_name))

    def push_span(span: typing.Optional[Span], endpoint_collection_enabled: bool) -> None:
        if span:
            ddup_push_span_id(span.span_id)
            if span._local_root is not None:
                ddup_push_local_root_span_id(span._local_root)
                ddup_push_trace_type(span._local_root.span_type)
                if endpoint_collection_enabled:
                    ddup_push_trace_resource_container(span._local_root._resource)

    def flush_sample() -> None:
        ddup_flush_sample()

    def upload() -> None:
        runtime_id = ensure_binary(runtime.get_runtime_id())
        ddup_set_runtime_id(runtime_id, len(runtime_id))
        ddup_upload()
