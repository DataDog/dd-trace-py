#include <echion/danger.h>
#include <echion/state.h>

#include <algorithm>
#include <cassert>
#include <cerrno>
#include <csetjmp>
#include <cstdio>
#include <signal.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static const size_t page_size = []() -> size_t {
    auto v = sysconf(_SC_PAGESIZE);

#ifdef PL_DARWIN
    if (v <= 0) {
        // Fallback on macOS just in case
        v = getpagesize();
    }
#endif

    if (v <= 0) {
        fprintf(stderr, "Failed to detect page size, falling back to 4096\n");
        return 4096;
    }

    return v;
}();

struct sigaction g_old_segv;
struct sigaction g_old_bus;

thread_local ThreadAltStack t_altstack;

// We "arm" by publishing a valid jmp env for this thread.
thread_local sigjmp_buf t_jmpenv;
thread_local volatile sig_atomic_t t_handler_armed = 0;

static inline void
arm_fault_handler()
{
    t_handler_armed = 1;
    __asm__ __volatile__("" ::: "memory");
}

static inline void
disarm_fault_handler()
{
    __asm__ __volatile__("" ::: "memory");
    t_handler_armed = 0;
}

static void
segv_handler(int signo, siginfo_t*, void*)
{
    if (!t_handler_armed) {
        struct sigaction* old = (signo == SIGSEGV) ? &g_old_segv : &g_old_bus;
        // Restore the previous handler and re-raise so default/old handling occurs.
        sigaction(signo, old, nullptr);
        raise(signo);
        return;
    }

    // Jump back to the armed site. Use 1 so sigsetjmp returns nonzero.
    siglongjmp(t_jmpenv, 1);
}

int
init_segv_catcher()
{
    if (t_altstack.ensure_installed() != 0) {
        return -1;
    }

    struct sigaction sa
    {};
    sa.sa_sigaction = segv_handler;
    sigemptyset(&sa.sa_mask);
    // SA_SIGINFO for 3-arg handler; SA_ONSTACK to run on alt stack; SA_NODEFER to avoid having to use savemask
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;

    // Check each handler separately to avoid overwriting g_old_segv/g_old_bus
    // with our own handler (which would cause infinite loops on unhandled signals).
    struct sigaction current;

    bool need_segv = true;
    if (sigaction(SIGSEGV, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        need_segv = false;
    }
    if (need_segv) {
        if (sigaction(SIGSEGV, &sa, &g_old_segv) != 0) {
            return -1;
        }
    }

    bool need_bus = true;
    if (sigaction(SIGBUS, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        need_bus = false;
    }
    if (need_bus) {
        if (sigaction(SIGBUS, &sa, &g_old_bus) != 0) {
            if (need_segv) {
                // Roll back SIGSEGV install on failure.
                sigaction(SIGSEGV, &g_old_segv, nullptr);
            }
            return -1;
        }
    }

    return 0;
}

#if defined PL_LINUX
using safe_memcpy_return_t = ssize_t;
#elif defined PL_DARWIN
using safe_memcpy_return_t = mach_vm_size_t;
#endif

safe_memcpy_return_t
safe_memcpy(void* dst, const void* src, size_t n)
{
    if (t_altstack.ensure_installed() != 0) {
        errno = EINVAL;
        return -1;
    }

    bool t_faulted = false;

    auto* d = static_cast<uint8_t*>(dst);
    auto* s = static_cast<const uint8_t*>(src);
    safe_memcpy_return_t rem = static_cast<safe_memcpy_return_t>(n);

    arm_fault_handler();
    if (sigsetjmp(t_jmpenv, /* save sig mask = */ 0) != 0) {
        // We arrived here from siglongjmp after a fault.
        t_faulted = true;
        goto landing;
    }

    // Copy in page-bounded chunks (at most one fault per bad page).
    while (rem) {
        safe_memcpy_return_t to_src_pg =
          page_size - (static_cast<uintptr_t>(reinterpret_cast<uintptr_t>(s)) & (page_size - 1));
        safe_memcpy_return_t to_dst_pg =
          page_size - (static_cast<uintptr_t>(reinterpret_cast<uintptr_t>(d)) & (page_size - 1));
        safe_memcpy_return_t chunk = std::min(rem, std::min(to_src_pg, to_dst_pg));

        // Optional early probe to fault before entering large memcpy
        (void)*reinterpret_cast<volatile const uint8_t*>(s);

        // If this faults, we'll siglongjmp back to the sigsetjmp above.
        (void)memcpy(d, s, static_cast<size_t>(chunk));

        d += chunk;
        s += chunk;
        rem -= chunk;
    }

landing:
    disarm_fault_handler();

    if (t_faulted) {
        errno = EFAULT;
        return -1;
    }

    return static_cast<safe_memcpy_return_t>(n);
}

#if defined PL_LINUX
ssize_t
safe_memcpy_wrapper(pid_t,
                    const struct iovec* __dstvec,
                    unsigned long int __dstiovcnt,
                    const struct iovec* __srcvec,
                    unsigned long int __srciovcnt,
                    unsigned long int)
{
    (void)__dstiovcnt;
    (void)__srciovcnt;
    assert(__dstiovcnt == 1);
    assert(__srciovcnt == 1);

    size_t to_copy = std::min(__dstvec->iov_len, __srcvec->iov_len);
    return safe_memcpy(__dstvec->iov_base, __srcvec->iov_base, to_copy);
}
#elif defined PL_DARWIN
kern_return_t
safe_memcpy_wrapper(vm_map_read_t target_task,
                    mach_vm_address_t address,
                    mach_vm_size_t size,
                    mach_vm_address_t data,
                    mach_vm_size_t* outsize)
{
    (void)target_task;

    auto copied =
      safe_memcpy(reinterpret_cast<void*>(data), reinterpret_cast<void*>(address), static_cast<size_t>(size));
    *outsize = copied;
    return copied == size ? KERN_SUCCESS : KERN_FAILURE;
}
#endif
