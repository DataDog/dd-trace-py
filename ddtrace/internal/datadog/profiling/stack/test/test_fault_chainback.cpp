#include "echion/danger.h"

#include <gtest/gtest.h>

#include <cerrno>
#include <csetjmp>
#include <csignal>
#include <cstdint>
#include <pthread.h>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>

namespace {

const size_t kGuardedPageSize = static_cast<size_t>(sysconf(_SC_PAGESIZE));

struct HostFaultReport
{
    volatile sig_atomic_t ran = 0;
    volatile sig_atomic_t signo = 0;
    volatile sig_atomic_t si_code = 0;
    void* fault_addr = nullptr;
};

thread_local sigjmp_buf t_host_jmpenv;
thread_local HostFaultReport* t_host_report = nullptr;

void
host_fault_handler(int signo, siginfo_t* info, void*)
{
    if (t_host_report != nullptr) {
        t_host_report->signo = signo;
        t_host_report->si_code = info != nullptr ? info->si_code : 0;
        t_host_report->fault_addr = info != nullptr ? info->si_addr : nullptr;
        t_host_report->ran = 1;
    }

    siglongjmp(t_host_jmpenv, 1);
}

int
install_host_handler(int signo)
{
    struct sigaction sa
    {};
    sa.sa_sigaction = host_fault_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    return sigaction(signo, &sa, nullptr);
}

bool
host_handler_owns(int signo)
{
    struct sigaction current
    {};
    if (sigaction(signo, nullptr, &current) != 0) {
        return false;
    }
    return current.sa_sigaction == host_fault_handler;
}

// Reads one byte from a PROT_NONE page, which faults with a known si_addr.
void
touch_guarded_page(void* page)
{
    volatile uint8_t sink = 0;
    sink = *static_cast<volatile const uint8_t*>(page);
    (void)sink;
}

// Outcome of the worker thread below. gtest fatal assertions are only safe on the main
// thread, so the worker records what happened and the test body asserts on it.
struct CedeOutcome
{
    bool page_mapped = false;
    bool host_installed = false;
    bool catcher_installed = false;
    bool profiler_owned_before_fault = false;
    bool faulted = false;
    void* page = nullptr;
};

void
run_cede_scenario(CedeOutcome& outcome, HostFaultReport& report)
{
    outcome.page = mmap(nullptr, kGuardedPageSize, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (outcome.page == MAP_FAILED) {
        outcome.page = nullptr;
        return;
    }
    outcome.page_mapped = true;

    if (install_host_handler(SIGSEGV) != 0 || install_host_handler(SIGBUS) != 0) {
        return;
    }
    outcome.host_installed = true;

    // The profiler installs over the host, saving the host disposition to chain back to.
    if (init_segv_catcher() != 0) {
        return;
    }
    outcome.catcher_installed = true;
    outcome.profiler_owned_before_fault = segv_handler_installed();

    t_host_report = &report;
    if (sigsetjmp(t_host_jmpenv, /* save sig mask = */ 1) == 0) {
        // Not inside safe_memcpy, so the profiler's handler is unarmed.
        touch_guarded_page(outcome.page);
    } else {
        outcome.faulted = true;
    }
    t_host_report = nullptr;
}

} // namespace

// A fault the profiler is not recovering from must reach the host with its original
// siginfo intact. Re-raising via pthread_kill reports si_code == SI_TKILL with a null
// fault address and a PC inside the profiler's handler, which stops a host such as the
// Go runtime from classifying the fault as recoverable and turns a nil-dereference panic
// into a fatal crash (PROF-15342).
//
// t_in_unarmed_chain latches after the first cede, so this scenario needs its own thread.
TEST(FaultChainback, CedesUnarmedFaultWithOriginalSiginfo)
{
    CedeOutcome outcome;
    HostFaultReport report;

    std::thread worker([&outcome, &report]() { run_cede_scenario(outcome, report); });
    worker.join();

    ASSERT_TRUE(outcome.page_mapped) << "could not map the guarded page";
    ASSERT_TRUE(outcome.host_installed) << "could not install the host fault handlers";
    ASSERT_TRUE(outcome.catcher_installed) << "init_segv_catcher failed";
    EXPECT_TRUE(outcome.profiler_owned_before_fault) << "the profiler did not take ownership of the fault signals";

    ASSERT_TRUE(outcome.faulted) << "the guarded page read did not fault";
    ASSERT_TRUE(report.ran) << "the host handler never received the fault";

    // Which signal a PROT_NONE read raises is platform-dependent: SIGSEGV on Linux,
    // SIGBUS on macOS. Either way it must arrive at the host, not be swallowed.
    const int delivered = report.signo;
    ASSERT_TRUE(delivered == SIGSEGV || delivered == SIGBUS) << "unexpected signal " << delivered;

    // A kernel-generated fault has si_code > 0. SI_TKILL (-6) here would mean the signal
    // was re-raised instead of being allowed to re-fault.
    EXPECT_GT(report.si_code, 0) << "si_code " << report.si_code << " is not a kernel-generated fault";
    EXPECT_EQ(report.fault_addr, outcome.page) << "si_addr did not survive the handoff to the host";

    // Cedes only the faulting signal back to the host. segv_handler_installed() requires
    // owning both SIGSEGV and SIGBUS, so it must read false even when we still hold the other.
    EXPECT_TRUE(host_handler_owns(delivered)) << "the profiler did not hand the signal back to the host";
    EXPECT_FALSE(segv_handler_installed()) << "the profiler still claims to own both fault signals";

    if (outcome.page != nullptr) {
        munmap(outcome.page, kGuardedPageSize);
    }
}

// The armed path is unchanged: a fault raised inside safe_memcpy is still recovered
// locally and reported as an error rather than handed to the host.
TEST(FaultChainback, SafeMemcpyStillRecoversArmedFault)
{
    ASSERT_EQ(install_host_handler(SIGSEGV), 0);
    ASSERT_EQ(install_host_handler(SIGBUS), 0);
    ASSERT_EQ(init_segv_catcher(), 0);
    ASSERT_TRUE(segv_handler_installed());

    void* page = mmap(nullptr, kGuardedPageSize, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    ASSERT_NE(page, MAP_FAILED);

    uint8_t dst[64] = { 0 };

#if defined PL_LINUX
    struct iovec iov_dst = { dst, sizeof(dst) };
    struct iovec iov_src = { page, sizeof(dst) };
    errno = 0;
    const ssize_t copied = safe_memcpy_wrapper(getpid(), &iov_dst, 1, &iov_src, 1, 0);
    EXPECT_EQ(copied, -1);
    EXPECT_EQ(errno, EFAULT);
#elif defined PL_DARWIN
    mach_vm_size_t outsize = 0;
    const kern_return_t kr = safe_memcpy_wrapper(mach_task_self(),
                                                 reinterpret_cast<mach_vm_address_t>(page),
                                                 sizeof(dst),
                                                 reinterpret_cast<mach_vm_address_t>(dst),
                                                 &outsize);
    EXPECT_NE(kr, KERN_SUCCESS);
#endif

    // Recovering locally must not disturb handler ownership.
    EXPECT_TRUE(segv_handler_installed());

    munmap(page, kGuardedPageSize);
}

// Injected signals have si_code <= 0 and must be re-delivered explicitly after cede;
// returning would swallow them because no faulting instruction re-executes.
TEST(FaultChainback, RedeliversInjectedSignalToHost)
{
    HostFaultReport report;

    ASSERT_EQ(install_host_handler(SIGSEGV), 0);
    ASSERT_EQ(install_host_handler(SIGBUS), 0);
    ASSERT_EQ(init_segv_catcher(), 0);
    ASSERT_TRUE(segv_handler_installed());

    t_host_report = &report;
    if (sigsetjmp(t_host_jmpenv, /* save sig mask = */ 1) == 0) {
        ASSERT_EQ(pthread_kill(pthread_self(), SIGSEGV), 0);
        FAIL() << "pthread_kill did not reach the host handler";
    }
    t_host_report = nullptr;

    ASSERT_TRUE(report.ran) << "the host handler never received the injected signal";
    EXPECT_EQ(report.signo, SIGSEGV);
    EXPECT_LE(report.si_code, 0) << "expected an injected delivery, got si_code " << report.si_code;
}

// A saved SIG_IGN disposition must survive injected deliveries; only synchronous faults
// upgrade it to SIG_DFL so the faulting instruction can terminate the process.
TEST(FaultChainback, PreservesSigIgnForInjectedDelivery)
{
    struct sigaction ign
    {};
    ign.sa_handler = SIG_IGN;
    sigemptyset(&ign.sa_mask);
    ASSERT_EQ(sigaction(SIGSEGV, &ign, nullptr), 0);
    ASSERT_EQ(sigaction(SIGBUS, &ign, nullptr), 0);

    ASSERT_EQ(init_segv_catcher(), 0);
    ASSERT_TRUE(segv_handler_installed());

    ASSERT_EQ(pthread_kill(pthread_self(), SIGSEGV), 0);

    struct sigaction current
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &current), 0);
    EXPECT_EQ(current.sa_handler, reinterpret_cast<void (*)(int)>(SIG_IGN));
}
