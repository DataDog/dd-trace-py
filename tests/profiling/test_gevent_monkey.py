from ddtrace.profiling import _threading as T
import ctypes
import importlib
import threading as safe_threading
import gevent.monkey

# Have a worker check its own TID in a tight loop
def worker():
  libc = ctypes.cdll.LoadLibrary('libc.so.6')
  gettid = libc.syscall
  gettid.argtypes = [ctypes.c_long, ctypes.c_long]
  gettid.restype = ctypes.c_long
  SYS_gettid = 186
  tid = gettid(SYS_gettid, 0)
  while True:
    T.get_thread_by_id(tid)

# Spawn worker
worker_thread = safe_threading.Thread(target=worker, args=())
worker_thread.start()

# Reload threading module in tight loop
while True:
  gevent.monkey.patch_all()

worker_thread.join()
