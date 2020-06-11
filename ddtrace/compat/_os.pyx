"""Implementation of os.register_at_fork using pthread_atfork on POSIX systems.

Be aware that this is not a strict equivalent of os.register_at_fork from Python.

The latter is handled by the interpreter and is safer. For example, it is run after the `threading.Lock` have been
released in the child process, which is *not* the case by this implementation.

This is only meant to be used to straightforward tasks with almost no side-effects.

"""

from __future__ import print_function


IF UNAME_SYSNAME in ("Linux", "Darwin", "FreeBSD", "OpenBSD", "NetBSD"):
    import sys

    cdef extern from "<pthread.h>":
        int pthread_atfork(object (*prepare)(), object (*parent)(), object (*child)());

    _before = []
    _after_in_child = []
    _after_in_parent = []

    def register_at_fork(before=None, after_in_child=None, after_in_parent=None):
        if before is not None:
            _before.insert(0, before)
        if after_in_child is not None:
            _after_in_child.append(after_in_child)
        if after_in_parent is not None:
            _after_in_parent.append(after_in_parent)

    cdef _run_handlers(handlers):
        for func in handlers:
            try:
                func()
            except Exception as e:
                # Mimic PyErr_WriteUnraisable
                print("Error running handler %s: %s" % (func, e), file=sys.stderr)

    cdef _pthread_atfork_prepare():
        _run_handlers(_before)

    cdef _pthread_atfork_after_in_parent():
        _run_handlers(_after_in_parent)

    cdef _pthread_atfork_after_in_child():
        _run_handlers(_after_in_child)

    pthread_atfork(&_pthread_atfork_prepare, &_pthread_atfork_after_in_parent, &_pthread_atfork_after_in_child)
