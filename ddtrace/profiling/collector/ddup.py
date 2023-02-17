from ctypes import *

libddprof = cdll.LoadLibrary("libddupload.so")

def init(service = "myservice", env = "prod", version = "custom"):
  if version is not None:
    version += ".libdatadog"
  libddprof.uploader_init(str.encode(service), str.encode(env), str.encode(version))

def start_sample():
  libddprof.start_sample()

def push_cputime(value, count):
  libddprof.push_cputime(c_ulonglong(value), count)

def push_walltime(value, count):
  libddprof.push_walltime(c_ulonglong(value), count)

def push_frame(name, filename, address, line):
  if name is None and filename is None:
    libddprof.push_frame(name, filename, address, line)
  elif filename is None:
    libddprof.push_frame(str.encode(name), filename, address, line)
  elif name is None:
    libddprof.push_frame(name, str.encode(filename), address, line)
  else:
    libddprof.push_frame(str.encode(name), str.encode(filename), address, line)

def push_threadinfo(thread_id, thread_native_id, thread_name):
  if thread_id is None:
    thread_id = 0
  if thread_native_id is None:
    thread_native_id = 0

  if thread_name is None:
    libddprof.push_threadinfo(c_ulonglong(thread_id), c_ulonglong(thread_native_id), thread_name)
  else:
    libddprof.push_threadinfo(c_ulonglong(thread_id), c_ulonglong(thread_native_id), str.encode(thread_name))

def push_taskinfo(task_id, task_name):
  if task_id is None:
    task_id = 0

  if task_name is None:
    libddprof.push_taskinfo(c_ulonglong(task_id), task_name)
  else:
    libddprof.push_taskinfo(c_ulonglong(task_id), str.encode(task_name))

def push_exceptioninfo(exception_type, count):
  if exception_type is not None:
    libddprof.push_exceptioninfo(str.encode(exception_type), c_ulonglong(count))

def push_classinfo(class_name):
  if class_name is not None:
    libddprof.push_exceptioninfo(str.encode(class_name))

def flush_sample():
  libddprof.flush_sample()

def upload():
  libddprof.upload()
