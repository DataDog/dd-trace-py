// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <atomic>
#include <cstdlib>
#include <cstring>

#include <echion/danger.h>

#if defined PL_LINUX
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
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

bool
is_truthy(const char* s);

bool
use_alternative_copy_memory();

// Whether safe_copy was actually set to the memcpy-based wrapper.
// This can be false even when use_alternative_copy_memory returns true,
// e.g. when init_segv_catcher fails.
inline bool fast_copy_active = false;

#if defined PL_LINUX
// Some checks are done at static initialization, so use this to read them at runtime
inline bool failed_safe_copy = false;

inline ssize_t (*safe_copy)(pid_t,
                            const struct iovec*,
                            unsigned long,
                            const struct iovec*,
                            unsigned long,
                            unsigned long) = process_vm_readv;

class VmReader
{
    void* buffer{ nullptr };
    size_t sz{ 0 };
    int fd{ -1 };
    inline static VmReader* instance{ nullptr }; // Prevents having to set this in implementation

    VmReader(size_t _sz, void* _buffer, int _fd)
      : buffer(_buffer)
      , sz{ _sz }
      , fd{ _fd }
    {
    }

    static VmReader* create(size_t sz);

    bool is_valid() const { return buffer != nullptr; }

  public:
    static VmReader* get_instance();

    ssize_t safe_copy(pid_t pid,
                      const struct iovec* local_iov,
                      unsigned long liovcnt,
                      const struct iovec* remote_iov,
                      unsigned long riovcnt,
                      unsigned long flags);

    ~VmReader()
    {
        if (buffer) {
            munmap(buffer, sz);
        }
        if (fd != -1) {
            close(fd);
        }
        instance = nullptr;
    }
};

/**
 * Initialize the safe copy operation on Linux
 */
bool
read_process_vm_init();

ssize_t
vmreader_safe_copy(pid_t pid,
                   const struct iovec* local_iov,
                   unsigned long liovcnt,
                   const struct iovec* remote_iov,
                   unsigned long riovcnt,
                   unsigned long flags);

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
 * This occurs at static init
 */
__attribute__((constructor)) inline void
init_safe_copy()
{
    if (use_alternative_copy_memory()) {
        if (init_segv_catcher() == 0) {
            safe_copy = safe_memcpy_wrapper;
            fast_copy_active = true;
            return;
        }

        std::cerr << "Failed to initialize segv catcher. Using process_vm_readv instead." << std::endl;
    }
}
#endif // if defined PL_DARWIN

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
