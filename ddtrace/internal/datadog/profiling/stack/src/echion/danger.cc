#define PY_SSIZE_T_CLEAN
#define Py_BUILD_CORE
#include <Python.h>

#include <echion/danger.h>
#include <echion/state.h>

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <csetjmp>
#include <cstdint>
#include <cstdio>
#include <pthread.h>
#include <signal.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

// Lock-free atomics are required to be async-signal-safe.
static_assert(std::atomic<int>::is_always_lock_free, "std::atomic<int> must be lock-free for use in signal handlers");

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

// Set once a saved SA_RESETHAND handler has run, meaning its disposition is now
// SIG_DFL. We cannot rewrite g_old_* from the handler: other threads may read it
// concurrently and see a torn struct. Atomic so that concurrent faults on
// several threads consume the one-shot handler at most once, as the kernel does.
static std::atomic<int> g_old_segv_reset{ 0 };
static std::atomic<int> g_old_bus_reset{ 0 };

thread_local ThreadAltStack t_altstack;

// We "arm" by publishing a valid jmp env for this thread.
thread_local sigjmp_buf t_jmpenv;
thread_local volatile sig_atomic_t t_handler_armed = 0;

// Guards against a signal-handler chaining cycle. The unarmed path below calls
// the previously installed handler; if that handler chains back
// to us (e.g. profiler <-> crashtracker pointing at each other), we would loop
// forever and hang the process. If we re-enter the unarmed path while already
// chaining, fall through to the default disposition so termination is guaranteed.
// We record the handler's frame address rather than a flag. If the previous
// handler recovers with longjmp (as opposed to returning), we cannot reset the flag
// and it would stay set forever.
// A real cycle always runs deeper on the stack than the recorded frame, and
// re-enters either through a direct call (same siginfo pointer) or a re-raise
// (user-sent siginfo). A fresh hardware fault has neither, so a stale frame left
// by a longjmp on a thread without an alt stack is not mistaken for a cycle.
thread_local volatile uintptr_t t_unarmed_chain_frame = 0;
thread_local siginfo_t* volatile t_unarmed_chain_info = nullptr;

static inline bool
is_user_sent(const siginfo_t* info)
{
    if (info == nullptr) {
        return false;
    }

#if defined PL_DARWIN
    return info->si_code == SI_USER || info->si_code == SI_QUEUE;
#else
    // SI_USER is 0; SI_QUEUE, SI_TKILL and other user-generated codes are negative.
    return info->si_code <= 0;
#endif
}

static inline void
reset_to_default_and_reraise(int signo)
{
    struct sigaction dfl
    {};
    dfl.sa_handler = SIG_DFL;
    sigemptyset(&dfl.sa_mask);
    dfl.sa_flags = 0;
    sigaction(signo, &dfl, nullptr);
    pthread_kill(pthread_self(), signo);
}

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
segv_handler(int signo, siginfo_t* info, void* ucontext)
{
    if (!t_handler_armed) {
        const uintptr_t frame = reinterpret_cast<uintptr_t>(__builtin_frame_address(0));
        if (t_unarmed_chain_frame != 0 && frame < t_unarmed_chain_frame &&
            (info == t_unarmed_chain_info || is_user_sent(info))) {
            // We are being re-entered while already chaining to a previous
            // handler: the handler chain has cycled back to us. Restore the
            // default disposition and re-raise to guarantee the process
            // terminates instead of looping forever.
            reset_to_default_and_reraise(signo);
            return;
        }

        // Saved and restored rather than cleared, so a legitimate nested fault
        // (a previous handler with SA_NODEFER faulting itself) does not wipe the
        // outer chain's state.
        const uintptr_t prev_frame = t_unarmed_chain_frame;
        siginfo_t* const prev_info = t_unarmed_chain_info;
        t_unarmed_chain_frame = frame;
        t_unarmed_chain_info = info;

        // Chain to the previous handler
        const struct sigaction* old = (signo == SIGSEGV) ? &g_old_segv : &g_old_bus;
        std::atomic<int>& old_reset = (signo == SIGSEGV) ? g_old_segv_reset : g_old_bus_reset;
        const bool reset = old_reset.load();

        // sa_handler and sa_sigaction share storage, so this also covers SA_SIGINFO handlers.
        bool old_is_dfl = reset || old->sa_handler == SIG_DFL;
        const bool old_is_ign = !reset && old->sa_handler == SIG_IGN;

        // Consume the one-shot handler without uninstalling ours, which safe_memcpy
        // still needs. If another thread claimed it first, it is now SIG_DFL.
        if (!old_is_dfl && !old_is_ign && (old->sa_flags & SA_RESETHAND) && old_reset.exchange(1) != 0) {
            old_is_dfl = true;
        }

        if (!old_is_dfl && !old_is_ign) {
            // Call the previous handler, but emulate kernel delivery
            // semantics based on the the previous handler configuration.
            // The mask is restored by sigreturn when we return.
            sigset_t mask = old->sa_mask;
            if (!(old->sa_flags & SA_NODEFER)) {
                sigaddset(&mask, signo);
            }
            pthread_sigmask(SIG_BLOCK, &mask, nullptr);

            if (old->sa_flags & SA_SIGINFO) {
                old->sa_sigaction(signo, info, ucontext);
            } else {
                old->sa_handler(signo);
            }
        } else if (old_is_ign && is_user_sent(info)) {
            // A user-sent signal can be ignored as requested: returning does not
            // re-execute a faulting instruction.
        } else {
            // SIG_IGN on a real fault is treated like SIG_DFL: returning from a
            // synchronous SIGSEGV/SIGBUS re-executes the faulting instruction and
            // would loop.
            reset_to_default_and_reraise(signo);
        }

        t_unarmed_chain_frame = prev_frame;
        t_unarmed_chain_info = prev_info;
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
    // The kernel decides whether to restart an interrupted syscall from the installed
    // action (ours), so mirror the previous handler's SA_RESTART.
    struct sigaction current
    {};

    bool need_segv = true;
    if (sigaction(SIGSEGV, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        need_segv = false;
    }
    if (need_segv) {
        struct sigaction sa_segv = sa;
        sa_segv.sa_flags |= current.sa_flags & SA_RESTART;
        if (sigaction(SIGSEGV, &sa_segv, &g_old_segv) != 0) {
            return -1;
        }
        g_old_segv_reset.store(0);
    }

    current = {};
    bool need_bus = true;
    if (sigaction(SIGBUS, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        need_bus = false;
    }
    if (need_bus) {
        struct sigaction sa_bus = sa;
        sa_bus.sa_flags |= current.sa_flags & SA_RESTART;
        if (sigaction(SIGBUS, &sa_bus, &g_old_bus) != 0) {
            if (need_segv) {
                // Roll back SIGSEGV install on failure.
                sigaction(SIGSEGV, &g_old_segv, nullptr);
            }
            return -1;
        }
        g_old_bus_reset.store(0);
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

// A one-shot (SA_RESETHAND) previous handler is claimed with the same exchange
// segv_handler uses, so a concurrent fault cannot run it while we also hand it
// back to the kernel, which would let it run a second time.
static const struct sigaction*
restorable_old_action(const struct sigaction& old, std::atomic<int>& reset, const struct sigaction& dfl)
{
    if ((old.sa_flags & SA_RESETHAND) && reset.exchange(1) != 0) {
        return &dfl;
    }

    return &old;
}

void
uninstall_segv_handler()
{
    // Restore the saved previous handlers, removing our handler from the chain.
    // This is used before letting another component (e.g., faulthandler) install
    // its own handler, so it saves the correct previous handler rather than ours.
    // After the other component installs, call init_segv_catcher to reinstall
    // ours on top, creating the correct non-cyclic chain.
    struct sigaction dfl
    {};
    dfl.sa_handler = SIG_DFL;
    sigemptyset(&dfl.sa_mask);
    dfl.sa_flags = 0;

    struct sigaction current;
    if (sigaction(SIGSEGV, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        sigaction(SIGSEGV, restorable_old_action(g_old_segv, g_old_segv_reset, dfl), nullptr);
    }
    if (sigaction(SIGBUS, nullptr, &current) == 0 && current.sa_sigaction == segv_handler) {
        sigaction(SIGBUS, restorable_old_action(g_old_bus, g_old_bus_reset, dfl), nullptr);
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
