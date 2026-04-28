// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <atomic>
#include <cstdlib>
#include <cstring>

#include <echion/danger.h>

#if defined PL_LINUX
#include <sys/types.h>
#include <sys/uio.h>
#include <unistd.h>

typedef pid_t proc_ref_t;

ssize_t
process_vm_readv(pid_t,
                 const struct iovec*,
                 unsigned long liovcnt,
                 const struct iovec* remote_iov,
                 unsigned long riovcnt,
                 unsigned long flags);

#define copy_type(addr, dest) (copy_memory(pid, addr, sizeof(dest), &dest))
#define copy_type_p(addr, dest) (copy_memory(pid, addr, sizeof(*dest), dest))
#define copy_generic(addr, dest, size)                                                                                 \
    (copy_memory(pid, reinterpret_cast<const void*>(addr), size, reinterpret_cast<void*>(dest)))

#elif defined PL_DARWIN
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/machine/kern_return.h>
#include <sys/sysctl.h>
#include <sys/types.h>

typedef mach_port_t proc_ref_t;

#define copy_type(addr, dest) (copy_memory(mach_task_self(), addr, sizeof(dest), &dest))
#define copy_type_p(addr, dest) (copy_memory(mach_task_self(), addr, sizeof(*dest), dest))
#define copy_generic(addr, dest, size) (copy_memory(mach_task_self(), (void*)(addr), size, (void*)(dest)))

inline kern_return_t (*safe_copy)(vm_map_read_t,
                                  mach_vm_address_t,
                                  mach_vm_size_t,
                                  mach_vm_address_t,
                                  mach_vm_size_t*) = mach_vm_read_overwrite;

#endif

// Whether safe_copy is currently set to the memcpy-based wrapper.
inline bool fast_copy_active = false;

// Whether init_segv_catcher succeeded at constructor time. Persists even if
// fast_copy_active is later toggled off by set_fast_copy_enabled().
inline bool safe_memcpy_initialized = false;

#if defined PL_LINUX
// Whether the process_vm_readv probe succeeded at constructor time.
inline bool process_vm_readv_available = false;

// True when neither safe_memcpy nor process_vm_readv could be initialized.
inline bool failed_safe_copy = false;

inline ssize_t (*safe_copy)(pid_t,
                            const struct iovec*,
                            unsigned long,
                            const struct iovec*,
                            unsigned long,
                            unsigned long) = process_vm_readv;

/**
 * Initialize the safe copy operation on Linux
 *
 * This occurs at static init
 */
__attribute__((constructor)) void
init_safe_copy();
#elif defined PL_DARWIN
/**
 * Initialize the safe copy operation on macOS
 *
 * Tries safe_memcpy first, falls back to mach_vm_read_overwrite.
 * This occurs at static init.
 */
__attribute__((constructor)) inline void
init_safe_copy()
{
    if (init_segv_catcher() == 0) {
        safe_copy = safe_memcpy_wrapper;
        fast_copy_active = true;
        safe_memcpy_initialized = true;
        return;
    }

    std::cerr << "Failed to initialize segv catcher. Using mach_vm_read_overwrite instead." << std::endl;
}
#endif // PL_DARWIN

// Switch the active copy method at runtime.  Must be called before the
// sampling thread is started (i.e. before Sampler::start()).
void
set_fast_copy_enabled(bool enabled);

/**
 * Copy a chunk of memory from a portion of the virtual memory of another
 * process.
 * @param proc_ref_t  the process reference (platform-dependent)
 * @param void *      the remote address
 * @param ssize_t     the number of bytes to read
 * @param void *      the destination buffer, expected to be at least as large
 *                    as the number of bytes to read.
 *
 * @return  zero on success, otherwise non-zero.
 */
#if defined(ECHION_FUZZING)
// Let the fuzzing harness control the copy_memory behavior, so we can simulate "garbage" reads.
extern "C" int
echion_fuzz_copy_memory(proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf);

inline int
copy_memory(proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf)
{
    return echion_fuzz_copy_memory(proc_ref, addr, len, buf);
}
#else
// Implementation in vm.cc
int
copy_memory(proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf);
#endif

inline pid_t pid = 0;

// Number of copy_memory errors since last drain (thread-safe, incremented from the sampling thread)
inline std::atomic<size_t> g_copy_memory_error_count{ 0 };

void
_set_pid(pid_t _pid);
