from libc.stdint cimport uint64_t, int64_t

cdef extern from "exporter.hpp":
    void ddup_uploader_init(const char *_service, const char *_env, const char *_version);
    void ddup_start_sample();
    void ddup_push_walltime(int64_t walltime, int64_t count);
    void ddup_push_cputime(int64_t cputime, int64_t count);
    void ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char *thread_name);
    void ddup_push_taskinfo(int64_t task_id, const char *task_name);
    void ddup_push_spaninfo(int64_t span_id, int64_t local_root_span_id);
    void ddup_push_traceinfo(const char *trace_type, const char *trace_resource_container);
    void ddup_push_exceptioninfo(const char *exception_type, int64_t count);
    void ddup_push_classinfo(const char *class_name);
    void ddup_push_frame(const char *_name, const char *_filename, uint64_t address, int64_t line);
    void ddup_flush_sample();
    void ddup_upload();

def init(service = "myservice", env = "prod", version = "custom"):
  if version is not None:
    version += ".libdatadog"
  ddup_uploader_init(str.encode(service), str.encode(env), str.encode(version))

def start_sample():
  ddup_start_sample()

def push_cputime(value, count):
  ddup_push_cputime(value, count)

def push_walltime(value, count):
  ddup_push_walltime(value, count)

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
    ddup_push_threadinfo(thread_id, thread_native_id, thread_name)
  else:
    ddup_push_threadinfo(thread_id, thread_native_id, str.encode(thread_name))

def push_taskinfo(task_id, task_name):
  if task_id is None:
    task_id = 0

  if task_name is None:
    ddup_push_taskinfo(task_id, task_name)
  else:
    ddup_push_taskinfo(task_id, str.encode(task_name))

def push_exceptioninfo(exception_type, count):
  if exception_type is not None:
    ddup_push_exceptioninfo(str.encode(exception_type), count)

def push_classinfo(class_name):
  if class_name is not None:
    ddup_push_exceptioninfo(str.encode(class_name), 1)

def flush_sample():
  ddup_flush_sample()

def upload():
  ddup_upload()
