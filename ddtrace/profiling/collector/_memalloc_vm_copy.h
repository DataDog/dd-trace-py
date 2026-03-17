#pragma once

#include <stddef.h>
#include <sys/types.h>

#if defined(PL_DARWIN) || defined(__APPLE__)
#include <mach/mach.h>
using memalloc_proc_ref_t = mach_port_t;
#else
#include <unistd.h>
using memalloc_proc_ref_t = pid_t;
#endif

extern memalloc_proc_ref_t pid;
int
copy_memory(memalloc_proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf);
void
_set_pid(pid_t _pid);

template<typename T>
static inline int
memalloc_copy_type(const void* addr, T& dest)
{
    return copy_memory(pid, addr, static_cast<ssize_t>(sizeof(T)), &dest);
}
