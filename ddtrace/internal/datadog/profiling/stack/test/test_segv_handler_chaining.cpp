#include "echion/danger.h"

#include <gtest/gtest.h>

#include <atomic>
#include <csignal>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/resource.h>
#include <thread>
#include <unistd.h>

namespace {

struct RestoreSignalHandlers
{
    struct sigaction segv
    {};
    struct sigaction bus
    {};

    RestoreSignalHandlers()
    {
        sigaction(SIGSEGV, nullptr, &segv);
        sigaction(SIGBUS, nullptr, &bus);
    }

    ~RestoreSignalHandlers()
    {
        sigaction(SIGSEGV, &segv, nullptr);
        sigaction(SIGBUS, &bus, nullptr);
    }
};

std::atomic<int> g_previous_owner_calls{ 0 };

void
previous_owner(int, siginfo_t*, void*)
{
    g_previous_owner_calls.fetch_add(1, std::memory_order_relaxed);
}

// A second handler in this same shared object, so that a check at library granularity
// would accept it as ours. Only comparing the function pointer we saved rejects it.
void
other_owner(int, siginfo_t*, void*)
{
}

struct sigaction g_captured_ours;

// Reinstates the disposition captured before the fault. A handler that saved ours and
// restores it on its way out closes the chain into a cycle, which is the case the
// re-entry branch of segv_handler exists to break.
void
chains_back_to_us(int signo, siginfo_t*, void*)
{
    sigaction(signo, &g_captured_ours, nullptr);
    pthread_kill(pthread_self(), signo);
}

int
install_handler(int signo, void (*handler)(int, siginfo_t*, void*))
{
    struct sigaction sa
    {};
    sa.sa_sigaction = handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_SIGINFO | SA_NODEFER;
    return sigaction(signo, &sa, nullptr);
}

} // namespace

// The cycle guard used to be a thread-local flag that was set on the first chain-back
// and never cleared, so a thread that survived one fault sent its next one to SIG_DFL
// and terminated the process without reaching the handler underneath. Scoping the guard
// to an install epoch releases the thread as soon as our handler is back in the chain.
TEST(SegvChainBack, EveryInstallEpochChainsToThePreviouslyInstalledHandler)
{
    RestoreSignalHandlers restore;
    g_previous_owner_calls.store(0, std::memory_order_relaxed);

    ASSERT_EQ(install_handler(SIGSEGV, previous_owner), 0);
    ASSERT_EQ(install_handler(SIGBUS, previous_owner), 0);

    // A fresh thread carries no recorded epoch, as a faulting worker would not.
    std::thread faulting([] {
        ASSERT_EQ(init_segv_catcher(), 0);
        ASSERT_TRUE(segv_handler_installed());

        // Unarmed: our handler restores the previous owner for the faulting signal and
        // re-raises, so the previous owner runs and we are left owning only SIGBUS.
        ASSERT_EQ(pthread_kill(pthread_self(), SIGSEGV), 0);
        ASSERT_EQ(g_previous_owner_calls.load(std::memory_order_relaxed), 1);
        ASSERT_FALSE(segv_handler_installed());

        ASSERT_EQ(init_segv_catcher(), 0);
        ASSERT_TRUE(segv_handler_installed());

        // Before the epoch this second fault took the re-entry branch and killed the
        // process through SIG_DFL instead of reaching the previous owner again.
        ASSERT_EQ(pthread_kill(pthread_self(), SIGSEGV), 0);
        EXPECT_EQ(g_previous_owner_calls.load(std::memory_order_relaxed), 2);
    });
    faulting.join();

    uninstall_segv_handler();
}

// Saving our own handler as the previous one would make the chain-back point back at
// us, which is the loop the epoch guard can only terminate, not avoid.
TEST(SegvChainBack, ReinstallingDoesNotOverwriteTheSavedPreviousHandler)
{
    RestoreSignalHandlers restore;

    ASSERT_EQ(install_handler(SIGSEGV, previous_owner), 0);
    ASSERT_EQ(install_handler(SIGBUS, previous_owner), 0);

    ASSERT_EQ(init_segv_catcher(), 0);
    ASSERT_TRUE(segv_handler_installed());
    ASSERT_EQ(init_segv_catcher(), 0);
    ASSERT_TRUE(segv_handler_installed());

    uninstall_segv_handler();

    struct sigaction current
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &current), 0);
    EXPECT_EQ(current.sa_sigaction, previous_owner);
    ASSERT_EQ(sigaction(SIGBUS, nullptr, &current), 0);
    EXPECT_EQ(current.sa_sigaction, previous_owner);
}

// The epoch is narrower than the old flag but still has to catch the case it was added
// for: a previous handler that chains straight back to us inside one delivery episode
// must reach the default disposition rather than loop forever.
TEST(SegvChainBackDeathTest, AChainThatCyclesWithinOneEpochFallsThroughToTheDefault)
{
    EXPECT_EXIT(
      {
          // A deliberate fatal signal; do not spend time writing a core for it.
          struct rlimit no_core
          {};
          no_core.rlim_cur = 0;
          no_core.rlim_max = 0;
          (void)setrlimit(RLIMIT_CORE, &no_core);

          if (install_handler(SIGSEGV, chains_back_to_us) != 0 || install_handler(SIGBUS, chains_back_to_us) != 0 ||
              init_segv_catcher() != 0 || sigaction(SIGSEGV, nullptr, &g_captured_ours) != 0) {
              _exit(2);
          }

          pthread_kill(pthread_self(), SIGSEGV);
          _exit(3);
      },
      ::testing::KilledBySignal(SIGSEGV),
      "");
}

// segv_handler_installed() needs both signals, so the split our own chain-back leaves
// behind is indistinguishable from a foreign takeover without this record.
TEST(SegvChainBack, TheChainBackIsRecordedAndConsumedExactlyOnce)
{
    RestoreSignalHandlers restore;
    // Tests share a process when the binary is run directly rather than through ctest.
    (void)consume_segv_handler_chained_back();

    ASSERT_EQ(install_handler(SIGSEGV, previous_owner), 0);
    ASSERT_EQ(install_handler(SIGBUS, previous_owner), 0);

    std::thread faulting([] {
        ASSERT_EQ(init_segv_catcher(), 0);
        // SIGBUS reaches the other half of the split the chain-back can leave.
        ASSERT_EQ(pthread_kill(pthread_self(), SIGBUS), 0);
        ASSERT_FALSE(segv_handler_installed());
    });
    faulting.join();

    EXPECT_TRUE(consume_segv_handler_chained_back());
    EXPECT_FALSE(consume_segv_handler_chained_back());

    uninstall_segv_handler();
}

// PROF-14568: a handler owned by anyone other than the component we saved is never
// taken back, whatever the chain-back record says.
TEST(ReclaimAfterChainBack, RefusesASignalOwnedBySomeoneOtherThanTheHandlerWeSaved)
{
    RestoreSignalHandlers restore;

    ASSERT_EQ(install_handler(SIGSEGV, previous_owner), 0);
    ASSERT_EQ(install_handler(SIGBUS, previous_owner), 0);
    ASSERT_EQ(init_segv_catcher(), 0);

    Dl_info saved{};
    Dl_info intruder{};
    ASSERT_NE(dladdr(reinterpret_cast<void*>(previous_owner), &saved), 0);
    ASSERT_NE(dladdr(reinterpret_cast<void*>(other_owner), &intruder), 0);
    ASSERT_EQ(saved.dli_fbase, intruder.dli_fbase);

    ASSERT_EQ(install_handler(SIGSEGV, other_owner), 0);
    EXPECT_FALSE(reclaim_after_chain_back());

    struct sigaction current
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &current), 0);
    EXPECT_EQ(current.sa_sigaction, other_owner);

    uninstall_segv_handler();
}

// The saved disposition is compared whole, so a previous owner that reinstalled itself
// with different flags no longer matches. That false negative only costs the slower
// syscall copy, which is the side to err on.
TEST(ReclaimAfterChainBack, RefusesWhenOnlyTheFlagsOfTheSavedHandlerChanged)
{
    RestoreSignalHandlers restore;

    ASSERT_EQ(install_handler(SIGSEGV, previous_owner), 0);
    ASSERT_EQ(install_handler(SIGBUS, previous_owner), 0);
    ASSERT_EQ(init_segv_catcher(), 0);

    struct sigaction reinstalled
    {};
    reinstalled.sa_sigaction = previous_owner;
    sigemptyset(&reinstalled.sa_mask);
    reinstalled.sa_flags = SA_SIGINFO | SA_NODEFER | SA_RESTART;
    ASSERT_EQ(sigaction(SIGSEGV, &reinstalled, nullptr), 0);

    EXPECT_FALSE(reclaim_after_chain_back());

    struct sigaction current
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &current), 0);
    EXPECT_EQ(current.sa_sigaction, previous_owner);
    EXPECT_NE(current.sa_flags & SA_RESTART, 0);

    uninstall_segv_handler();
}

TEST(ReclaimAfterChainBack, RestoresBothSignalsWhenTheSplitIsTheOneOurChainBackLeaves)
{
    RestoreSignalHandlers restore;

    ASSERT_EQ(install_handler(SIGSEGV, previous_owner), 0);
    ASSERT_EQ(install_handler(SIGBUS, previous_owner), 0);
    struct sigaction saved
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &saved), 0);

    ASSERT_EQ(init_segv_catcher(), 0);
    ASSERT_TRUE(segv_handler_installed());

    // Exactly the state the unarmed path leaves: the faulting signal back on the owner
    // we saved for it, the other one still ours.
    ASSERT_EQ(sigaction(SIGSEGV, &saved, nullptr), 0);
    ASSERT_FALSE(segv_handler_installed());

    EXPECT_TRUE(reclaim_after_chain_back());
    EXPECT_TRUE(segv_handler_installed());

    uninstall_segv_handler();

    struct sigaction current
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &current), 0);
    EXPECT_EQ(current.sa_sigaction, previous_owner);
    ASSERT_EQ(sigaction(SIGBUS, nullptr, &current), 0);
    EXPECT_EQ(current.sa_sigaction, previous_owner);
}
