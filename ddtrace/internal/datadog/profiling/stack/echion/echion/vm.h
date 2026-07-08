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

// User wants fast copy (set at init); survives warmup toggling fast_copy_active.
inline bool fast_copy_requested = false;

// User opted out via _DD_PROFILING_STACK_FAST_COPY or set_fast_copy(false).
inline bool fast_copy_user_disabled = false;

// Sticky: fell back to syscall copy (init failure, foreign handler, warmup miss).
inline bool fast_copy_syscall_fallback = false;

inline void
mark_fast_copy_syscall_fallback()
{
    fast_copy_syscall_fallback = true;
}

// Persistent intent: not toggled by warmup/fallback; survives fork() (PROF-14568).
inline bool fast_copy_desired = false;

// Sticky flag set once a foreign (non-cooperative) SIGSEGV/SIGBUS handler has taken
// ownership and the sampler has surrendered to the syscall copy. It is intentionally
// permanent for the lifetime of the process (and inherited across fork()): once we
// have deferred to a foreign owner we must stop reclaiming the handler, otherwise a
// later cooperative swap (e.g. faulthandler.disable()) could hand ownership back to us
// on top of that foreign handler and let a restart/fork re-enable safe_memcpy over it
// (PROF-14568). Gates handler reclaim and the fast-copy warmup/upgrade.
inline bool fast_copy_foreign_takeover = false;

// Whether init_segv_catcher succeeded at constructor time. Persists even if
// fast_copy_active is later toggled off by set_fast_copy_enabled.
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
#endif // PL_LINUX

/**
 * Initialize the safe copy operation on macOS
 *
 * Tries safe_memcpy first (unless disabled via env var), falls back to
 * mach_vm_read_overwrite.
 * This occurs at static init.
 */
__attribute__((constructor)) void
init_safe_copy();

// Switch the active copy method at runtime.  Must be called before the
// sampling thread is started.
// Returns true on success, false if the requested method (and its fallback) are unavailable
// (in which case failed_safe_copy is set and stack profiling should be disabled).
bool
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
