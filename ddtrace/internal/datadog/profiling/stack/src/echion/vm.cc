#include <echion/vm.h>

#if defined PL_LINUX
static bool
probe_process_vm_readv()
{
    char src[128];
    char dst[128];
    for (size_t i = 0; i < 128; i++) {
        src[i] = 0x41;
        dst[i] = ~0x42;
    }

    struct iovec iov_dst = { dst, sizeof(dst) };
    struct iovec iov_src = { src, sizeof(src) };
    ssize_t result = process_vm_readv(getpid(), &iov_dst, 1, &iov_src, 1, 0);
    return result == static_cast<ssize_t>(sizeof(src));
}

__attribute__((constructor)) void
init_safe_copy()
{
    // Always probe process_vm_readv so we know whether it is a valid fallback.
    process_vm_readv_available = probe_process_vm_readv();

    // Try safe_memcpy (fast path) first.
    if (init_segv_catcher() == 0) {
        safe_copy = safe_memcpy_wrapper;
        fast_copy_active = true;
        safe_memcpy_initialized = true;
    } else {
        // std::cerr might not have been fully initialized at this point.
        fprintf(stderr, "Failed to initialize segv catcher. Trying process_vm_readv.\n");
        if (process_vm_readv_available) {
            safe_copy = process_vm_readv;
        } else {
            fprintf(stderr, "Failed to initialize safe copy interface\n");
            failed_safe_copy = true;
        }
    }
}
#endif // PL_LINUX

void
set_fast_copy_enabled(bool enabled)
{
    if (enabled) {
        if (safe_memcpy_initialized) {
            safe_copy = safe_memcpy_wrapper;
            fast_copy_active = true;
        } else {
            fprintf(stderr, "Warning: fast copy requested but safe_memcpy was not initialized\n");
        }
    } else {
#if defined PL_LINUX
        safe_copy = process_vm_readv;
        fast_copy_active = false;
        if (!process_vm_readv_available) {
            fprintf(stderr, "Warning: process_vm_readv was not verified during init\n");
        }
#elif defined PL_DARWIN
        safe_copy = mach_vm_read_overwrite;
        fast_copy_active = false;
#endif
    }
}

int
copy_memory(proc_ref_t proc_ref, const void* addr, ssize_t len, void* buf)
{
    ssize_t result = -1;

    // Early exit on zero length
    if (len <= 0) {
        return 0;
    }

    // Early exit on zero page
    if (reinterpret_cast<uintptr_t>(addr) < 4096) {
        return static_cast<int>(result);
    }

#if defined PL_LINUX
    struct iovec local[1];
    struct iovec remote[1];

    local[0].iov_base = buf;
    local[0].iov_len = len;
    remote[0].iov_base = const_cast<void*>(addr);
    remote[0].iov_len = len;

    result = safe_copy(proc_ref, local, 1, remote, 1, 0);

#elif defined PL_DARWIN
    kern_return_t kr = safe_copy(proc_ref,
                                 reinterpret_cast<mach_vm_address_t>(addr),
                                 len,
                                 reinterpret_cast<mach_vm_address_t>(buf),
                                 reinterpret_cast<mach_vm_size_t*>(&result));

    if (kr != KERN_SUCCESS) {
        g_copy_memory_error_count.fetch_add(1, std::memory_order_relaxed);
        return -1;
    }

#endif

    int ret = len != result;
    if (ret) {
        g_copy_memory_error_count.fetch_add(1, std::memory_order_relaxed);
    }
    return ret;
}

void
_set_pid(pid_t _pid)
{
    pid = _pid;
}
