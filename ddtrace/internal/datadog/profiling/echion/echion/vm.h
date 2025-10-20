// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <cstring>
#include <string>

#if defined PL_LINUX
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <unistd.h>
#include <algorithm>

typedef pid_t proc_ref_t;

ssize_t process_vm_readv(pid_t, const struct iovec*, unsigned long liovcnt,
                         const struct iovec* remote_iov, unsigned long riovcnt,
                         unsigned long flags);

#define copy_type(addr, dest) (copy_memory(pid, addr, sizeof(dest), &dest))
#define copy_type_p(addr, dest) (copy_memory(pid, addr, sizeof(*dest), dest))
#define copy_generic(addr, dest, size) (copy_memory(pid, (void*)(addr), size, (void*)(dest)))

#elif defined PL_DARWIN
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/machine/kern_return.h>
#include <sys/sysctl.h>
#include <sys/types.h>

typedef mach_port_t proc_ref_t;

#define copy_type(addr, dest) (copy_memory(mach_task_self(), addr, sizeof(dest), &dest))
#define copy_type_p(addr, dest) (copy_memory(mach_task_self(), addr, sizeof(*dest), dest))
#define copy_generic(addr, dest, size) \
    (copy_memory(mach_task_self(), (void*)(addr), size, (void*)(dest)))
#endif

// Some checks are done at static initialization, so use this to read them at runtime
inline bool failed_safe_copy = false;

#if defined PL_LINUX
inline ssize_t (*safe_copy)(pid_t, const struct iovec*, unsigned long, const struct iovec*,
                            unsigned long, unsigned long) = process_vm_readv;

class VmReader
{
    void* buffer{nullptr};
    size_t sz{0};
    int fd{-1};
    inline static VmReader* instance{nullptr};  // Prevents having to set this in implementation

    VmReader(size_t _sz, void* _buffer, int _fd) : buffer(_buffer), sz{_sz}, fd{_fd}
    {
    }

    static VmReader* create(size_t sz)
    {
        // Makes a temporary file and ftruncates it to the specified size
        std::array<std::string, 3> tmp_dirs = {"/dev/shm", "/tmp", "/var/tmp"};
        std::string tmp_suffix = "/echion-XXXXXX";

        int fd = -1;
        void* ret = nullptr;

        for (auto& tmp_dir : tmp_dirs)
        {
            // Reset the file descriptor, just in case
            close(fd);
            fd = -1;

            // Create the temporary file
            std::string tmpfile = tmp_dir + tmp_suffix;
            fd = mkstemp(tmpfile.data());
            if (fd == -1)
                continue;

            // Unlink might fail if delete is blocked on the VFS, but currently no action is taken
            unlink(tmpfile.data());

            // Make sure we have enough size
            if (ftruncate(fd, sz) == -1)
            {
                continue;
            }

            // Map the file
            ret = mmap(NULL, sz, PROT_READ | PROT_WRITE, MAP_PRIVATE, fd, 0);
            if (ret == MAP_FAILED)
            {
                ret = nullptr;
                continue;
            }

            // Successful.  Break.
            break;
        }

        return new VmReader(sz, ret, fd);
    }
    
    bool is_valid() const { return buffer != nullptr; }

public:
    static VmReader* get_instance()
    {
        if (instance == nullptr)
        {
            instance = VmReader::create(1024 * 1024);  // A megabyte?
            if (!instance)
            {
                std::cerr << "Failed to initialize VmReader with buffer size " << instance->sz << std::endl;
                return nullptr;
            }
        }

        return instance;
    }

    ssize_t safe_copy(pid_t pid, const struct iovec* local_iov, unsigned long liovcnt,
                      const struct iovec* remote_iov, unsigned long riovcnt, unsigned long flags)
    {
        (void)pid;
        (void)flags;
        if (liovcnt != 1 || riovcnt != 1)
        {
            // Unsupported
            return 0;
        }

        // Check to see if we need to resize the buffer
        if (remote_iov[0].iov_len > sz)
        {
            if (ftruncate(fd, remote_iov[0].iov_len) == -1)
            {
                return 0;
            }
            else
            {
                void* tmp = mremap(buffer, sz, remote_iov[0].iov_len, MREMAP_MAYMOVE);
                if (tmp == MAP_FAILED)
                {
                    return 0;
                }
                buffer = tmp;  // no need to munmap
                sz = remote_iov[0].iov_len;
            }
        }

        ssize_t ret = pwritev(fd, remote_iov, riovcnt, 0);
        if (ret == -1)
        {
            return ret;
        }

        // Copy the data from the buffer to the remote process
        std::memcpy(local_iov[0].iov_base, buffer, local_iov[0].iov_len);
        return ret;
    }

    ~VmReader()
    {
        if (buffer)
        {
            munmap(buffer, sz);
        }
        if (fd != -1)
        {
            close(fd);
        }
        instance = nullptr;
    }
};

/**
 * Initialize the safe copy operation on Linux
 */
inline bool read_process_vm_init()
{
    VmReader* _ = VmReader::get_instance();
    return !!_;
}

inline ssize_t vmreader_safe_copy(pid_t pid, const struct iovec* local_iov, unsigned long liovcnt,
                                  const struct iovec* remote_iov, unsigned long riovcnt,
                                  unsigned long flags)
{
    auto reader = VmReader::get_instance();
    if (!reader)
        return 0;
    return reader->safe_copy(pid, local_iov, liovcnt, remote_iov, riovcnt, flags);
}

/**
 * Initialize the safe copy operation on Linux
 *
 * This occurs at static init
 */
__attribute__((constructor)) inline void init_safe_copy()
{
    char src[128];
    char dst[128];
    for (size_t i = 0; i < 128; i++)
    {
        src[i] = 0x41;
        dst[i] = ~0x42;
    }

    // Check to see that process_vm_readv works, unless it's overridden
    const char force_override_str[] = "ECHION_ALT_VM_READ_FORCE";
    const std::array<std::string, 6> truthy_values = {"1",  "true",   "yes",
                                                      "on", "enable", "enabled"};
    const char* force_override = std::getenv(force_override_str);
    if (!force_override || std::find(truthy_values.begin(), truthy_values.end(), force_override) ==
                               truthy_values.end())
    {
        struct iovec iov_dst = {dst, sizeof(dst)};
        struct iovec iov_src = {src, sizeof(src)};
        ssize_t result = process_vm_readv(getpid(), &iov_dst, 1, &iov_src, 1, 0);

        // If we succeed, then use process_vm_readv
        if (result == sizeof(src))
        {
            safe_copy = process_vm_readv;
            return;
        }
    }

    // Else, we have to setup the writev method
    if (!read_process_vm_init())
    {
        // std::cerr might not have been fully initialized at this point, so use
        // fprintf instead.
        fprintf(stderr, "Failed to initialize all safe copy interfaces\n");
        failed_safe_copy = true;
        return;
    }

    safe_copy = vmreader_safe_copy;
}
#endif

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
static inline int copy_memory(proc_ref_t proc_ref, void* addr, ssize_t len, void* buf)
{
    ssize_t result = -1;

    // Early exit on zero page
    if (reinterpret_cast<uintptr_t>(addr) < 4096)
    {
        return result;
    }

#if defined PL_LINUX
    struct iovec local[1];
    struct iovec remote[1];

    local[0].iov_base = buf;
    local[0].iov_len = len;
    remote[0].iov_base = addr;
    remote[0].iov_len = len;

    result = safe_copy(proc_ref, local, 1, remote, 1, 0);

#elif defined PL_DARWIN
    kern_return_t kr = mach_vm_read_overwrite(proc_ref, (mach_vm_address_t)addr, len,
                                              (mach_vm_address_t)buf, (mach_vm_size_t*)&result);

    if (kr != KERN_SUCCESS)
        return -1;

#endif

    return len != result;
}

inline pid_t pid = 0;

inline void _set_pid(pid_t _pid)
{
    pid = _pid;
}
