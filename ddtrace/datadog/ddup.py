import os
import inspect
from ctypes import *

libddup = cdll.LoadLibrary(os.path.dirname(os.path.abspath(inspect.stack()[0][1])) + "/libddup.so")

def init(service = "myservice", env = "prod", version = "custom"):
  if version is not None:
    version += ".libdatadog"
  libddup.uploader_init(str.encode(service), str.encode(env), str.encode(version))

def start_sample():
  libddup.start_sample()

def push_cputime(value, count):
  libddup.push_cputime(c_ulonglong(value), count)

def push_walltime(value, count):
  libddup.push_walltime(c_ulonglong(value), count)

def push_frame(name, filename, address, line):
  if name is None and filename is None:
    libddup.push_frame(name, filename, address, line)
  elif filename is None:
    libddup.push_frame(str.encode(name), filename, address, line)
  elif name is None:
    libddup.push_frame(name, str.encode(filename), address, line)
  else:
    libddup.push_frame(str.encode(name), str.encode(filename), address, line)

def push_threadinfo(thread_id, thread_native_id, thread_name):
  if thread_id is None:
    thread_id = 0
  if thread_native_id is None:
    thread_native_id = 0

  if thread_name is None:
    libddup.push_threadinfo(c_ulonglong(thread_id), c_ulonglong(thread_native_id), thread_name)
  else:
    libddup.push_threadinfo(c_ulonglong(thread_id), c_ulonglong(thread_native_id), str.encode(thread_name))

def push_taskinfo(task_id, task_name):
  if task_id is None:
    task_id = 0

  if task_name is None:
    libddup.push_taskinfo(c_ulonglong(task_id), task_name)
  else:
    libddup.push_taskinfo(c_ulonglong(task_id), str.encode(task_name))

def push_exceptioninfo(exception_type, count):
  if exception_type is not None:
    libddup.push_exceptioninfo(str.encode(exception_type), c_ulonglong(count))

def push_classinfo(class_name):
  if class_name is not None:
    libddup.push_exceptioninfo(str.encode(class_name))

def flush_sample():
  libddup.flush_sample()

def upload():
  libddup.upload()
