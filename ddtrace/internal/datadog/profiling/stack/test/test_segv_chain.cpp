#include "echion/danger.h"

#include <gtest/gtest.h>

#include <atomic>
#include <csetjmp>
#include <csignal>
#include <cstdint>
#include <ctime>
#include <new>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

// These tests install a "previous" SIGSEGV/SIGBUS handler, put ours on top with
// init_segv_catcher, and check that faults we do not recover are chained to the
// previous handler with the semantics the kernel would have applied to it.

#if defined(__SANITIZE_THREAD__)
#define DD_TSAN 1
#elif defined(__has_feature)
#if __has_feature(thread_sanitizer)
#define DD_TSAN 1
#endif
#endif

namespace {

std::atomic<int> g_calls{ 0 };
std::atomic<int> g_last_signo{ 0 };
std::atomic<void*> g_last_fault_addr{ nullptr };
std::atomic<int> g_last_si_code{ 0 };
std::atomic<bool> g_last_ucontext_set{ false };
sigset_t g_mask_in_handler;
std::atomic<int>* g_shared_calls = nullptr;
struct sigaction g_ours
{};
thread_local sigjmp_buf t_recover;

void
counting_handler(int signo, siginfo_t*, void*)
{
    g_last_signo.store(signo);
    g_calls.fetch_add(1);
}

void
plain_handler(int signo)
{
    g_last_signo.store(signo);
    g_calls.fetch_add(1);
}

void
slow_counting_handler(int, siginfo_t*, void*)
{
    g_calls.fetch_add(1);
    struct timespec ts
    {
        0, 100 * 1000 * 1000
    };
    nanosleep(&ts, nullptr);
}

void
recovering_handler(int, siginfo_t* info, void* ucontext)
{
    g_calls.fetch_add(1);
    g_last_fault_addr.store(info != nullptr ? info->si_addr : nullptr);
    g_last_si_code.store(info != nullptr ? info->si_code : 0);
    g_last_ucontext_set.store(ucontext != nullptr);
    siglongjmp(t_recover, 1);
}

// Chains back to our handler by calling it directly, as a previous handler that
// saved ours as its own previous one would (e.g. crashtracker in a cycle).
void
calling_back_handler(int signo, siginfo_t* info, void* ucontext)
{
    g_shared_calls->fetch_add(1);
    g_ours.sa_sigaction(signo, info, ucontext);
}

// Chains back to our handler by reinstalling it and re-raising, as faulthandler
// would in a cycle.
void
reraising_handler(int signo, siginfo_t*, void*)
{
    g_shared_calls->fetch_add(1);
    sigaction(signo, &g_ours, nullptr);
    pthread_kill(pthread_self(), signo);
}

void
mask_recording_handler(int, siginfo_t*, void*)
{
    pthread_sigmask(SIG_BLOCK, nullptr, &g_mask_in_handler);
    g_calls.fetch_add(1);
}

void
slow_one_shot_handler(int, siginfo_t*, void*)
{
    g_shared_calls->fetch_add(1);
    // Stay inside long enough for the other thread's signal to arrive concurrently.
    struct timespec ts
    {
        0, 200 * 1000 * 1000
    };
    nanosleep(&ts, nullptr);
}

int
install_previous_action(const struct sigaction& sa)
{
    if (sigaction(SIGSEGV, &sa, nullptr) != 0 || sigaction(SIGBUS, &sa, nullptr) != 0) {
        return -1;
    }
    if (init_segv_catcher() != 0) {
        return -1;
    }
    return sigaction(SIGSEGV, nullptr, &g_ours);
}

int
install_previous(void (*fn)(int, siginfo_t*, void*), int extra_flags, const sigset_t* mask)
{
    struct sigaction sa
    {};
    sa.sa_sigaction = fn;
    sa.sa_flags = SA_SIGINFO | extra_flags;
    if (mask != nullptr) {
        sa.sa_mask = *mask;
    } else {
        sigemptyset(&sa.sa_mask);
    }
    return install_previous_action(sa);
}

int
install_previous_plain(void (*fn)(int))
{
    struct sigaction sa
    {};
    sa.sa_handler = fn;
    sa.sa_flags = 0;
    sigemptyset(&sa.sa_mask);
    return install_previous_action(sa);
}

std::atomic<int>*
map_shared_counter()
{
    void* mem = mmap(nullptr, sizeof(std::atomic<int>), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) {
        return nullptr;
    }
    return new (mem) std::atomic<int>(0);
}

void
unmap_shared_counter(std::atomic<int>* counter)
{
    munmap(counter, sizeof(std::atomic<int>));
}

// Runs body in a forked child and returns its wait status. Used for outcomes that
// terminate the process. The alarm turns a hang (e.g. a chaining loop) into SIGALRM.
template<typename Body>
int
run_in_child(Body&& body)
{
    const pid_t pid = fork();
    if (pid < 0) {
        return -1;
    }
    if (pid == 0) {
        struct rlimit no_core
        {
            0, 0
        };
        setrlimit(RLIMIT_CORE, &no_core);
        alarm(5);
        body();
        _exit(0);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid) {
        return -1;
    }
    return status;
}

bool
killed_by(int status, int signo)
{
    return WIFSIGNALED(status) && WTERMSIG(status) == signo;
}

// A PROT_NONE access is reported as SIGSEGV on Linux and SIGBUS on macOS.
bool
killed_by_fault(int status)
{
    return killed_by(status, SIGSEGV) || killed_by(status, SIGBUS);
}

bool
safe_copy_from(const void* src)
{
    uint8_t dst[16];
#if defined PL_LINUX
    struct iovec dstvec
    {
        dst, sizeof(dst)
    };
    struct iovec srcvec
    {
        const_cast<void*>(src), sizeof(dst)
    };
    return safe_memcpy_wrapper(0, &dstvec, 1, &srcvec, 1, 0) == static_cast<ssize_t>(sizeof(dst));
#elif defined PL_DARWIN
    mach_vm_size_t copied = 0;
    return safe_memcpy_wrapper(mach_task_self(),
                               reinterpret_cast<mach_vm_address_t>(src),
                               sizeof(dst),
                               reinterpret_cast<mach_vm_address_t>(dst),
                               &copied) == KERN_SUCCESS;
#endif
}

// Recurses before faulting so that a later fault runs deeper on the stack than an earlier one.
__attribute__((noinline)) int
fault_at_depth(volatile uint8_t* bad, int depth)
{
    volatile char pad[256];
    pad[0] = static_cast<char>(depth);
    if (depth > 0) {
        return fault_at_depth(bad, depth - 1) + pad[0];
    }
    return *bad;
}

bool
is_blocked(int signo)
{
    sigset_t cur;
    pthread_sigmask(SIG_BLOCK, nullptr, &cur);
    return sigismember(&cur, signo) == 1;
}

class SegvChain : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        uninstall_segv_handler();
        sigaction(SIGSEGV, nullptr, &saved_segv_);
        sigaction(SIGBUS, nullptr, &saved_bus_);

        bad_page_ = mmap(nullptr, getpagesize(), PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        ASSERT_NE(bad_page_, MAP_FAILED);

        g_calls.store(0);
        g_last_signo.store(0);
        g_last_fault_addr.store(nullptr);
        g_last_si_code.store(0);
        g_last_ucontext_set.store(false);
        sigemptyset(&g_mask_in_handler);
    }

    void TearDown() override
    {
        uninstall_segv_handler();
        sigaction(SIGSEGV, &saved_segv_, nullptr);
        sigaction(SIGBUS, &saved_bus_, nullptr);
        munmap(bad_page_, getpagesize());
    }

    struct sigaction saved_segv_
    {};
    struct sigaction saved_bus_
    {};
    void* bad_page_ = nullptr;
};

} // namespace

// A previous handler that returns must not remove ours: safe_memcpy must still
// recover its own faults afterwards, without them reaching the previous handler.
TEST_F(SegvChain, PreviousHandlerReturnsAndSafeMemcpyStillRecovers)
{
    ASSERT_EQ(install_previous(counting_handler, 0, nullptr), 0);
    ASSERT_TRUE(segv_handler_installed());

    pthread_kill(pthread_self(), SIGSEGV);
    EXPECT_EQ(g_calls.load(), 1);
    EXPECT_TRUE(segv_handler_installed());

    EXPECT_FALSE(safe_copy_from(bad_page_));
    EXPECT_EQ(g_calls.load(), 1);

    const uint8_t good[16] = {};
    EXPECT_TRUE(safe_copy_from(good));
}

// A previous handler that recovers with siglongjmp leaves our chain state stale.
// On a thread without an alternate signal stack, a later fault deeper on the
// stack must still reach the previous handler rather than be treated as a cycle.
TEST_F(SegvChain, PreviousHandlerRecoversWithLongjmpAndLaterFaultsStillReachIt)
{
    ASSERT_EQ(install_previous(recovering_handler, 0, nullptr), 0);

    auto* bad = static_cast<volatile uint8_t*>(bad_page_);
    int recovered = 0;
    // A new thread has no alternate signal stack, so our handler runs on the normal stack.
    std::thread worker([&]() {
        if (sigsetjmp(t_recover, 1) == 0) {
            fault_at_depth(bad, 0);
        } else {
            recovered++;
        }
        if (sigsetjmp(t_recover, 1) == 0) {
            fault_at_depth(bad, 64);
        } else {
            recovered++;
        }
    });
    worker.join();

    EXPECT_EQ(recovered, 2);
    EXPECT_EQ(g_calls.load(), 2);
    // The previous handler sees the original hardware fault, not a re-raised signal.
    EXPECT_EQ(g_last_fault_addr.load(), bad_page_);
    EXPECT_GT(g_last_si_code.load(), 0);
    EXPECT_TRUE(g_last_ucontext_set.load());
}

// The previous handler's sa_mask and the signal itself (no SA_NODEFER) must be
// blocked while it runs, and the caller's mask must be restored afterwards.
TEST_F(SegvChain, PreviousHandlerMaskIsAppliedAndRestored)
{
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGUSR1);
    ASSERT_EQ(install_previous(mask_recording_handler, 0, &mask), 0);
    ASSERT_FALSE(is_blocked(SIGUSR1));
    ASSERT_FALSE(is_blocked(SIGSEGV));

    pthread_kill(pthread_self(), SIGSEGV);

    ASSERT_EQ(g_calls.load(), 1);
    EXPECT_EQ(sigismember(&g_mask_in_handler, SIGUSR1), 1);
    EXPECT_EQ(sigismember(&g_mask_in_handler, SIGSEGV), 1);
    EXPECT_FALSE(is_blocked(SIGUSR1));
    EXPECT_FALSE(is_blocked(SIGSEGV));
}

// A previous handler with SA_NODEFER must be able to receive the same signal
// while it runs, and we must not leave it blocked afterwards.
TEST_F(SegvChain, PreviousHandlerWithNoDeferKeepsSignalUnblocked)
{
#if defined(DD_TSAN)
    GTEST_SKIP() << "TSan installs every handler with a full sa_mask, so SA_NODEFER has no effect";
#endif
    ASSERT_EQ(install_previous(mask_recording_handler, SA_NODEFER, nullptr), 0);

    pthread_kill(pthread_self(), SIGSEGV);

    ASSERT_EQ(g_calls.load(), 1);
    EXPECT_EQ(sigismember(&g_mask_in_handler, SIGSEGV), 0);
    EXPECT_FALSE(is_blocked(SIGSEGV));
}

// A previous SA_RESETHAND handler must run at most once even when two threads
// receive the signal concurrently. The second delivery then sees SIG_DFL and
// terminates the process, so this runs in a child.
TEST_F(SegvChain, ResetHandPreviousHandlerRunsAtMostOnceAcrossThreads)
{
#if defined PL_DARWIN
    GTEST_SKIP() << "macOS does not report SA_RESETHAND through sigaction, so it cannot be emulated";
#endif
    std::atomic<int>* shared_calls = map_shared_counter();
    ASSERT_NE(shared_calls, nullptr);

    const int status = run_in_child([&]() {
        g_shared_calls = shared_calls;
        if (install_previous(slow_one_shot_handler, SA_RESETHAND, nullptr) != 0) {
            _exit(2);
        }

        std::atomic<bool> go{ false };
        auto raise_on_self = [&]() {
            while (!go.load()) {
            }
            pthread_kill(pthread_self(), SIGSEGV);
        };
        std::thread a(raise_on_self);
        std::thread b(raise_on_self);
        go.store(true);
        a.join();
        b.join();
        // Unreachable if the one-shot handler was consumed: the second delivery is fatal.
    });

    EXPECT_TRUE(killed_by(status, SIGSEGV)) << "status=" << status;
    // 0 is possible: the thread that claimed the handler can be preempted before
    // it increments, while the other delivery already terminates the process.
    EXPECT_LE(shared_calls->load(), 1);
    unmap_shared_counter(shared_calls);
}

// The kernel restarts interrupted syscalls based on the installed action, which
// is ours, so we must mirror the previous handler's SA_RESTART.
TEST_F(SegvChain, PreviousHandlerSaRestartIsMirrored)
{
    ASSERT_EQ(install_previous(counting_handler, SA_RESTART, nullptr), 0);
    struct sigaction installed
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &installed), 0);
    EXPECT_NE(installed.sa_flags & SA_RESTART, 0);

    uninstall_segv_handler();
    ASSERT_EQ(install_previous(counting_handler, 0, nullptr), 0);
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &installed), 0);
    EXPECT_EQ(installed.sa_flags & SA_RESTART, 0);
}

// A previous handler that calls our handler directly forms a cycle. We must
// terminate the process instead of recursing until the stack overflows.
TEST_F(SegvChain, DirectCallCycleTerminates)
{
    std::atomic<int>* shared_calls = map_shared_counter();
    ASSERT_NE(shared_calls, nullptr);

    const int status = run_in_child([&]() {
        g_shared_calls = shared_calls;
        if (install_previous(calling_back_handler, 0, nullptr) != 0) {
            _exit(2);
        }
        pthread_kill(pthread_self(), SIGSEGV);
    });

    EXPECT_TRUE(killed_by(status, SIGSEGV)) << "status=" << status;
    EXPECT_EQ(shared_calls->load(), 1);
    unmap_shared_counter(shared_calls);
}

// A previous handler that reinstalls ours and re-raises forms a cycle. We must
// terminate the process instead of looping.
TEST_F(SegvChain, ReraiseCycleTerminates)
{
#if defined(DD_TSAN)
    // Without SA_NODEFER the re-raised signal stays pending until our handler returns
    // and then arrives as a fresh delivery, which is not detectable as a cycle.
    GTEST_SKIP() << "TSan installs every handler with a full sa_mask, so SA_NODEFER has no effect";
#endif
    std::atomic<int>* shared_calls = map_shared_counter();
    ASSERT_NE(shared_calls, nullptr);

    const int status = run_in_child([&]() {
        g_shared_calls = shared_calls;
        if (install_previous(reraising_handler, SA_NODEFER, nullptr) != 0) {
            _exit(2);
        }
        pthread_kill(pthread_self(), SIGSEGV);
    });

    EXPECT_TRUE(killed_by(status, SIGSEGV)) << "status=" << status;
    EXPECT_EQ(shared_calls->load(), 1);
    unmap_shared_counter(shared_calls);
}

// A user-sent signal with a previous SIG_IGN disposition is ignored, as the kernel
// would. macOS reports user-sent SIGSEGV with the same si_code as a real fault, so
// there we terminate rather than risk looping on a real fault.
TEST_F(SegvChain, PreviousIgnoreHandlesUserSentSignal)
{
#if defined PL_DARWIN
    const int status = run_in_child([&]() {
        if (install_previous_plain(SIG_IGN) != 0) {
            _exit(2);
        }
        pthread_kill(pthread_self(), SIGSEGV);
    });
    EXPECT_TRUE(killed_by(status, SIGSEGV)) << "status=" << status;
#else
    ASSERT_EQ(install_previous_plain(SIG_IGN), 0);

    pthread_kill(pthread_self(), SIGSEGV);

    EXPECT_TRUE(segv_handler_installed());
#endif
}

// A real fault cannot be ignored: returning would re-execute the faulting
// instruction forever. A previous SIG_IGN is treated as SIG_DFL.
TEST_F(SegvChain, PreviousIgnoreTerminatesOnRealFault)
{
    auto* bad = static_cast<volatile uint8_t*>(bad_page_);
    const int status = run_in_child([&]() {
        if (install_previous_plain(SIG_IGN) != 0) {
            _exit(2);
        }
        (void)*bad;
    });

    EXPECT_TRUE(killed_by_fault(status)) << "status=" << status;
}

// A real fault with a previous SIG_DFL disposition terminates the process with that signal.
TEST_F(SegvChain, PreviousDefaultTerminatesOnRealFault)
{
    auto* bad = static_cast<volatile uint8_t*>(bad_page_);
    const int status = run_in_child([&]() {
        if (install_previous_plain(SIG_DFL) != 0) {
            _exit(2);
        }
        (void)*bad;
    });

    EXPECT_TRUE(killed_by_fault(status)) << "status=" << status;
}

// Once a one-shot previous handler has run, uninstalling ours must restore
// SIG_DFL rather than the consumed handler. The other signal was not consumed.
TEST_F(SegvChain, UninstallAfterResetHandConsumedRestoresDefault)
{
#if defined PL_DARWIN
    GTEST_SKIP() << "macOS does not report SA_RESETHAND through sigaction, so it cannot be emulated";
#endif
    ASSERT_EQ(install_previous(counting_handler, SA_RESETHAND, nullptr), 0);
    pthread_kill(pthread_self(), SIGSEGV);
    ASSERT_EQ(g_calls.load(), 1);

    uninstall_segv_handler();

    struct sigaction segv
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &segv), 0);
    EXPECT_EQ(segv.sa_handler, SIG_DFL);

    struct sigaction bus
    {};
    ASSERT_EQ(sigaction(SIGBUS, nullptr, &bus), 0);
    EXPECT_EQ(bus.sa_sigaction, counting_handler);
}

// A one-shot previous handler that has not run is handed back to the kernel on uninstall.
TEST_F(SegvChain, UninstallBeforeResetHandConsumedRestoresPrevious)
{
    ASSERT_EQ(install_previous(counting_handler, SA_RESETHAND, nullptr), 0);

    uninstall_segv_handler();

    struct sigaction segv
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &segv), 0);
    EXPECT_EQ(segv.sa_sigaction, counting_handler);
    EXPECT_EQ(g_calls.load(), 0);
}

// A previous handler installed without SA_SIGINFO is called with the one-argument signature.
TEST_F(SegvChain, PreviousPlainHandlerIsCalled)
{
    ASSERT_EQ(install_previous_plain(plain_handler), 0);

    pthread_kill(pthread_self(), SIGSEGV);

    EXPECT_EQ(g_calls.load(), 1);
    EXPECT_EQ(g_last_signo.load(), SIGSEGV);
}

// SIGBUS is chained to its own saved previous handler.
TEST_F(SegvChain, SigbusIsChained)
{
    ASSERT_EQ(install_previous(counting_handler, 0, nullptr), 0);

    pthread_kill(pthread_self(), SIGBUS);

    EXPECT_EQ(g_calls.load(), 1);
    EXPECT_EQ(g_last_signo.load(), SIGBUS);
    EXPECT_TRUE(segv_handler_installed());
}

// Chain state is per thread: concurrent deliveries on two threads must both
// reach the previous handler, without one being mistaken for a cycle.
TEST_F(SegvChain, ConcurrentDeliveriesOnTwoThreadsBothReachPrevious)
{
    ASSERT_EQ(install_previous(slow_counting_handler, 0, nullptr), 0);

    std::atomic<bool> go{ false };
    auto raise_on_self = [&]() {
        while (!go.load()) {
        }
        pthread_kill(pthread_self(), SIGSEGV);
    };
    std::thread a(raise_on_self);
    std::thread b(raise_on_self);
    go.store(true);
    a.join();
    b.join();

    EXPECT_EQ(g_calls.load(), 2);
}

// Calling init_segv_catcher again must not save our own handler as the previous
// one, which would make the chain call itself. Runs in a child because that
// regression would terminate the process.
TEST_F(SegvChain, RepeatedInitDoesNotChainToItself)
{
    const int status = run_in_child([&]() {
        if (install_previous(counting_handler, 0, nullptr) != 0 || init_segv_catcher() != 0) {
            _exit(2);
        }
        pthread_kill(pthread_self(), SIGSEGV);
        _exit(g_calls.load() == 1 ? 0 : 3);
    });

    EXPECT_TRUE(WIFEXITED(status) && WEXITSTATUS(status) == 0) << "status=" << status;
}
