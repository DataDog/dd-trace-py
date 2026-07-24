/*
 * Python frame walker.
 *
 * Reconstructs the Python call stack of a thread from outside the process by
 * reading its memory through /proc/<pid>/mem. The walk is:
 *
 *   /proc/<pid>/maps  -> locate libpython / the python binary
 *   ELF .symtab       -> address of _PyRuntime
 *   _PyRuntime        -> PyInterpreterState
 *   tstate chain      -> match PyThreadState by thread_id == tid
 *   frame chain       -> per-frame PyCodeObject
 *   PyCodeObject      -> co_qualname (3.11+) / co_name (<= 3.10)
 *
 * Struct member offsets differ per Python minor version, so they are kept in a
 * version-keyed table selected at startup from the detected interpreter
 * version. Target: Python 3.10-3.12.
 *
 * Spike milestone 4: time.sleep / lock.acquire visible as Python frames.
 * Reference: FHP interpreter/python/python.go, py-spy src/python_spy.rs.
 */
#ifndef DDOFFCPU_PYSYM_H
#define DDOFFCPU_PYSYM_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

struct pysym;

/*
 * Attach to a process and locate its interpreter. Returns NULL if the process
 * is not a supported CPython, or its layout could not be resolved.
 */
struct pysym*
pysym_new(pid_t pid);

void
pysym_free(struct pysym* py);

/*
 * Walk the Python stack for a single thread. Writes up to max_frames qualified
 * names (newest frame first) into frames, each NUL-terminated and at most
 * name_len bytes. Returns the number of frames written, or -1 on error.
 */
int
pysym_walk(struct pysym* py, pid_t tid, char (*frames)[256], int max_frames);

#endif /* DDOFFCPU_PYSYM_H */
