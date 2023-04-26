import os
import platform

import ddtrace
from ddtrace.internal import runtime

from libc.stdint cimport uint64_t, int64_t

IF UNAME_SYSNAME == "Linux" and UNAME_MACHINE == "x86_64":
    cdef extern from "exporter.hpp":
        void ddup_uploader_init(const char *_service, const char *_env, const char *_version, const char *_runtime, const char *_runtime_version, const char *profiler_version);
        void ddup_start_sample();
        void ddup_push_walltime(int64_t walltime, int64_t count);
        void ddup_push_cputime(int64_t cputime, int64_t count);
        void ddup_push_acquire(int64_t acquire_time, int64_t count);
        void ddup_push_release(int64_t release_time, int64_t count);
        void ddup_push_alloc(int64_t alloc_size, int64_t count);
        void ddup_push_heap(int64_t heap_size);
        void ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char *thread_name);
        void ddup_push_taskinfo(int64_t task_id, const char *task_name);
        void ddup_push_spaninfo(int64_t span_id, int64_t local_root_span_id);
        void ddup_push_traceinfo(const char *trace_type, const char *trace_resource_container);
        void ddup_push_exceptioninfo(const char *exception_type, int64_t count);
        void ddup_push_classinfo(const char *class_name);
        void ddup_push_frame(const char *_name, const char *_filename, uint64_t address, int64_t line);
        void ddup_flush_sample();
        void ddup_set_runtime_id(const char *_id);
        void ddup_upload();

    def init(str service = "myservice", str env = "prod", str version = "custom"):
      if version is not None:
        version += ".libdatadog"
      runtime = platform.python_implementation()
      runtime_version = platform.python_version()
      ddup_uploader_init(str.encode(service), str.encode(env), str.encode(version), str.encode(runtime), str.encode(runtime_version), str.encode(ddtrace.__version__))

    def start_sample():
      ddup_start_sample()

    def push_cputime(value, count):
      ddup_push_cputime(value, count)

    def push_walltime(value, count):
      ddup_push_walltime(value, count)

    def push_acquire(value, count):
      ddup_push_acquire(value, count)

    def push_release(value, count):
      ddup_push_release(value, count)

    def push_alloc(value, count):
      ddup_push_alloc(value, count)

    def push_heap(value):
      ddup_push_heap(value)

    def push_frame(name, filename, address, line):
      if name is None and filename is None:
        ddup_push_frame(name, filename, address, line)
      elif filename is None:
        ddup_push_frame(str.encode(name), filename, address, line)
      elif name is None:
        ddup_push_frame(name, str.encode(filename), address, line)
      else:
        ddup_push_frame(str.encode(name), str.encode(filename), address, line)

    def push_threadinfo(thread_id, thread_native_id, thread_name):
      if thread_id is None:
        thread_id = 0
      if thread_native_id is None:
        thread_native_id = 0

      if thread_name is None:
        ddup_push_threadinfo(thread_id, thread_native_id, str.encode(""))
      else:
        ddup_push_threadinfo(thread_id, thread_native_id, str.encode(thread_name))

    def push_taskinfo(task_id, task_name):
      if task_id is None:
        task_id = 0

      if task_name is None:
        ddup_push_taskinfo(task_id, task_name)
      else:
        ddup_push_taskinfo(task_id, str.encode(task_name))

    def push_exceptioninfo(exception_type: type, count):
      if exception_type is not None:
        ddup_push_exceptioninfo(str.encode(exception_type.__name__), count)

    def push_classinfo(str class_name):
      if class_name is not None:
        ddup_push_exceptioninfo(str.encode(class_name), 1)

    def flush_sample():
      ddup_flush_sample()

    def upload():
      cdef bytes runtime_id = str.encode(runtime.get_runtime_id())
      ddup_set_runtime_id(runtime_id)
      ddup_upload()

ELSE:
    def init(bytes service = b"myservice", bytes env = b"prod", bytes version = b"custom"):
        pass

    def start_sample():
        pass

    def push_cputime(value, count):
        pass

    def push_walltime(value, count):
        pass

    def push_acquire(value, count):
        pass

    def push_release(value, count):
        pass

    def push_alloc(value, count):
        pass

    def push_heap(value):
        pass

    def push_frame(name, filename, address, line):
        pass

    def push_threadinfo(thread_id, thread_native_id, thread_name):
        pass

    def push_taskinfo(task_id, task_name):
        pass

    def push_exceptioninfo(exception_type, count):
        pass

    def push_classinfo(class_name):
        pass

    def flush_sample():
        pass

    def upload():
        pass
