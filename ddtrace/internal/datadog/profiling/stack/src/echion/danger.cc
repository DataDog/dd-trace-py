#include <echion/danger.h>
#include <echion/state.h>

#include <algorithm>
#include <cassert>
#include <cerrno>
#include <csetjmp>
#include <cstdio>
#include <pthread.h>
#include <signal.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static const size_t page_size = []() -> size_t {
    auto v = sysconf(_SC_PAGESIZE);

#ifdef PL_DARWIN
    if (v <= 0) {
        // sysconf(_SC_PAGESIZE) can return -1; getpagesize() is the BSD API on Darwin
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

// Set once this thread has ceded a fault signal to its previous owner. Re-entering the
// unarmed path afterwards means something reinstalled us and the fault is not making
// progress, so we fall through to the default disposition and termination is guaranteed
// instead of looping forever.
thread_local volatile sig_atomic_t t_in_unarmed_chain = 0;

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
install_default_disposition(int signo)
{
    struct sigaction dfl
    {};
    dfl.sa_handler = SIG_DFL;
    sigemptyset(&dfl.sa_mask);
    dfl.sa_flags = 0;
    sigaction(signo, &dfl, nullptr);
}

// Kernel-generated faults that re-execute the faulting instruction when the handler
// returns. Injected signals (si_code <= 0) and a few positive Linux codes that are
// still asynchronous must be re-delivered explicitly instead.
static bool
is_synchronous_fault(int signo, const siginfo_t* info)
{
    if (info == nullptr || info->si_code <= 0) {
        return false;
    }
#if defined PL_LINUX
#ifdef SEGV_MTEAERR
    if (signo == SIGSEGV && info->si_code == SEGV_MTEAERR) {
        return false;
    }
#endif
#ifdef BUS_MCEERR_AO
    if (signo == SIGBUS && info->si_code == BUS_MCEERR_AO) {
        return false;
    }
#endif
#endif
    return true;
}

// Hands signo back to whoever owned it before us, removing us from the chain.
//
// A saved SIG_IGN disposition is replaced by SIG_DFL only for synchronous faults:
// ignoring one leaves the faulting instruction to re-execute forever, so honoring it
// would hang the process instead of terminating it. Asynchronous deliveries keep SIG_IGN.
static void
cede_fault_signal(int signo, const siginfo_t* info)
{
    const struct sigaction* saved = (signo == SIGSEGV) ? &g_old_segv : &g_old_bus;

    if ((saved->sa_flags & SA_SIGINFO) == 0 && saved->sa_handler == SIG_IGN) {
        if (is_synchronous_fault(signo, info)) {
            install_default_disposition(signo);
            return;
        }
        sigaction(signo, saved, nullptr);
        return;
    }

    sigaction(signo, saved, nullptr);
}

static void
segv_handler(int signo, siginfo_t* info, void*)
{
    if (!t_handler_armed) {
        if (t_in_unarmed_chain) {
            // We already ceded this signal on this thread, so something reinstalled us
            // and the fault is not making progress. Force the default disposition to
            // guarantee the process terminates instead of looping forever.
            install_default_disposition(signo);
            if (!is_synchronous_fault(signo, info)) {
                pthread_kill(pthread_self(), signo);
            }
            return;
        }
        t_in_unarmed_chain = 1;

        // This fault is not one of ours to recover, so give the signal back to its
        // previous owner and return without re-raising. For a synchronous kernel fault
        // returning re-executes the faulting instruction, so the new owner receives a
        // genuine signal carrying the original si_code, si_addr and fault PC.
        //
        // Re-raising with pthread_kill instead delivers si_code == SI_TKILL with no fault
        // address and a PC inside this handler. Hosts that read siginfo to classify a
        // fault cannot recover from that: the Go runtime turns it into a fatal error
        // rather than the nil-dereference panic it would otherwise raise (PROF-15342).
        cede_fault_signal(signo, info);

        if (!is_synchronous_fault(signo, info)) {
            // Not a synchronous fault (SI_USER, SI_QUEUE, SI_TKILL, ...): no instruction
            // will re-execute, so returning would swallow the signal. Re-deliver it
            // thread-directed, which is async-signal-safe per POSIX unlike raise. The
            // signal was already injected, so there is no fault siginfo to preserve.
            pthread_kill(pthread_self(), signo);
        }

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

bool
segv_handler_installed()
{
    // Recovery needs our handler to own BOTH SIGSEGV and SIGBUS
    // (a copy fault can arrive as either); anything else means we can't recover.
    const int signals[] = { SIGSEGV, SIGBUS };
    for (int signo : signals) {
        struct sigaction current;
        if (sigaction(signo, nullptr, &current) != 0) {
            return false;
        }
        if (current.sa_sigaction != segv_handler || (current.sa_flags & SA_SIGINFO) == 0) {
            return false;
        }
    }
    return true;
}

void
uninstall_segv_handler()
{
    // Restore the saved previous handlers, removing our handler from the chain.
    // This is used before letting another component (e.g., faulthandler) install
    // its own handler, so it saves the correct previous handler rather than ours.
    // After the other component installs, call init_segv_catcher to reinstall
    // ours on top, creating the correct non-cyclic chain.
    struct sigaction current;
    if (sigaction(SIGSEGV, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        sigaction(SIGSEGV, &g_old_segv, nullptr);
    }
    if (sigaction(SIGBUS, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        sigaction(SIGBUS, &g_old_bus, nullptr);
    }
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
        // Values are always <= page_size, so the unsigned-to-signed narrowing is safe.
        safe_memcpy_return_t to_src_pg = static_cast<safe_memcpy_return_t>(
          page_size - (static_cast<uintptr_t>(reinterpret_cast<uintptr_t>(s)) & (page_size - 1)));
        safe_memcpy_return_t to_dst_pg = static_cast<safe_memcpy_return_t>(
          page_size - (static_cast<uintptr_t>(reinterpret_cast<uintptr_t>(d)) & (page_size - 1)));
        safe_memcpy_return_t chunk = std::min(rem, std::min(to_src_pg, to_dst_pg));

        // Optional early probe to fault before entering large memcpy
        (void)*static_cast<volatile const uint8_t*>(s);

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
                    const struct iovec* dstvec,
                    unsigned long int dstiovcnt,
                    const struct iovec* srcvec,
                    unsigned long int srciovcnt,
                    unsigned long int)
{
    (void)dstiovcnt;
    (void)srciovcnt;
    assert(dstiovcnt == 1);
    assert(srciovcnt == 1);

    size_t to_copy = std::min(dstvec->iov_len, srcvec->iov_len);
    return safe_memcpy(dstvec->iov_base, srcvec->iov_base, to_copy);
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
