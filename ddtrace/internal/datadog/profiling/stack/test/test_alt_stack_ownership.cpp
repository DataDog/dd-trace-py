#include "echion/danger.h"

#include <gtest/gtest.h>

#include <csignal>
#include <string>
#include <sys/mman.h>

namespace {

constexpr size_t kSentinelSize = 1 << 20; // 1 MiB

void
disable_alt_stack()
{
    stack_t disable{};
    disable.ss_flags = SS_DISABLE;
    sigaltstack(&disable, nullptr);
}

} // namespace

// When an alternate signal stack is already installed (by the application, faulthandler, or
// crashtracker), ThreadAltStack adopts it without owning the mapping, and the destructor must not
// disable it. Without the owns_mapping guard the destructor would strip the adopted stack during
// thread-local cleanup and break the owner's fault handling.
TEST(ThreadAltStackOwnership, DestructorDoesNotDisableAdoptedAltStack)
{
    // Start from a known state: no alt stack installed on this thread.
    disable_alt_stack();

    void* foreign = mmap(nullptr, kSentinelSize, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    ASSERT_NE(foreign, MAP_FAILED);

    // Pre-install a foreign alt stack, as another component would.
    stack_t foreign_ss{};
    foreign_ss.ss_sp = foreign;
    foreign_ss.ss_size = kSentinelSize;
    foreign_ss.ss_flags = 0;
    ASSERT_EQ(sigaltstack(&foreign_ss, nullptr), 0);

    {
        ThreadAltStack alt;
        ASSERT_EQ(alt.ensure_installed(), 0);
        // A stack was already present, so ThreadAltStack adopts it and does not own the mapping.
        ASSERT_FALSE(alt.owns_mapping);

        // alt goes out of scope here; its destructor runs.
    }

    // The adopted foreign alt stack must still be installed and not disabled.
    stack_t cur{};
    ASSERT_EQ(sigaltstack(nullptr, &cur), 0);
    EXPECT_EQ(cur.ss_sp, foreign);
    EXPECT_EQ(cur.ss_flags & SS_DISABLE, 0);

    disable_alt_stack();
    munmap(foreign, kSentinelSize);
}

// When ThreadAltStack allocated and owns the alt stack, the destructor disables and frees it at
// thread-local cleanup.
TEST(ThreadAltStackOwnership, DestructorDisablesOwnAltStack)
{
    disable_alt_stack();

    void* owned_mem = nullptr;
    {
        ThreadAltStack alt;
        ASSERT_EQ(alt.ensure_installed(), 0);
        ASSERT_TRUE(alt.owns_mapping);
        owned_mem = alt.mem;

        stack_t cur{};
        ASSERT_EQ(sigaltstack(nullptr, &cur), 0);
        ASSERT_EQ(cur.ss_sp, owned_mem);
        ASSERT_EQ(cur.ss_flags & SS_DISABLE, 0);

        // alt goes out of scope here; its destructor runs and should disable our alt stack.
    }

    stack_t cur{};
    ASSERT_EQ(sigaltstack(nullptr, &cur), 0);
    EXPECT_NE(cur.ss_flags & SS_DISABLE, 0);
}

// If another component (for example crashtracker) replaces this thread's alternate signal stack
// after ThreadAltStack installed its own, the destructor must not disable that replacement; it
// only frees our own mapping. Without the cur.ss_sp == mem guard the destructor would strip the
// replacement owner's alt stack.
TEST(ThreadAltStackOwnership, DestructorDoesNotDisableReplacedAltStack)
{
    disable_alt_stack();

    void* sentinel = mmap(nullptr, kSentinelSize, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    ASSERT_NE(sentinel, MAP_FAILED);

    {
        ThreadAltStack alt;
        ASSERT_EQ(alt.ensure_installed(), 0);
        // No alt stack was present, so ThreadAltStack allocated and owns its own mapping.
        ASSERT_TRUE(alt.owns_mapping);
        ASSERT_NE(alt.mem, nullptr);

        // Simulate another component replacing this thread's alt stack after we installed ours.
        stack_t replacement{};
        replacement.ss_sp = sentinel;
        replacement.ss_size = kSentinelSize;
        replacement.ss_flags = 0;
        ASSERT_EQ(sigaltstack(&replacement, nullptr), 0);

        // alt goes out of scope here; its destructor runs.
    }

    // The replacement must still be installed and not disabled.
    stack_t cur{};
    ASSERT_EQ(sigaltstack(nullptr, &cur), 0);
    EXPECT_EQ(cur.ss_sp, sentinel);
    EXPECT_EQ(cur.ss_flags & SS_DISABLE, 0);

    disable_alt_stack();
    munmap(sentinel, kSentinelSize);
}

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

void
foreign_siginfo_handler(int, siginfo_t*, void*)
{
}

void
foreign_one_arg_handler(int)
{
}

} // namespace

TEST(DescribeSegvHandlerOwners, NamesDefaultIgnoredDdtraceAndForeign)
{
    RestoreSignalHandlers restore;

    struct sigaction sa
    {};
    sa.sa_handler = SIG_DFL;
    ASSERT_EQ(sigaction(SIGSEGV, &sa, nullptr), 0);
    sa.sa_handler = SIG_IGN;
    ASSERT_EQ(sigaction(SIGBUS, &sa, nullptr), 0);

    const std::string def_ign = describe_segv_handler_owners();
    EXPECT_NE(def_ign.find("SIGSEGV=SIG_DFL"), std::string::npos);
    EXPECT_NE(def_ign.find("SIGBUS=SIG_IGN"), std::string::npos);

    ASSERT_EQ(init_segv_catcher(), 0);
    EXPECT_EQ(describe_segv_handler_owners(), "SIGSEGV=ddtrace, SIGBUS=ddtrace");

    struct sigaction stripped_sa
    {};
    ASSERT_EQ(sigaction(SIGSEGV, nullptr, &stripped_sa), 0);
    stripped_sa.sa_flags &= ~SA_SIGINFO;
    ASSERT_EQ(sigaction(SIGSEGV, &stripped_sa, nullptr), 0);
    ASSERT_EQ(sigaction(SIGBUS, nullptr, &stripped_sa), 0);
    stripped_sa.sa_flags &= ~SA_SIGINFO;
    ASSERT_EQ(sigaction(SIGBUS, &stripped_sa, nullptr), 0);
    const std::string stripped = describe_segv_handler_owners();
    EXPECT_EQ(stripped, "SIGSEGV=ddtrace+missing_sa_siginfo, SIGBUS=ddtrace+missing_sa_siginfo");
    EXPECT_EQ(stripped.find("+0x"), std::string::npos);
    uninstall_segv_handler();

    struct sigaction foreign
    {};
    foreign.sa_sigaction = foreign_siginfo_handler;
    sigemptyset(&foreign.sa_mask);
    foreign.sa_flags = SA_SIGINFO;
    ASSERT_EQ(sigaction(SIGSEGV, &foreign, nullptr), 0);
    ASSERT_EQ(sigaction(SIGBUS, &foreign, nullptr), 0);

    const std::string named = describe_segv_handler_owners();
    EXPECT_NE(named.find("test_alt_stack_ownership"), std::string::npos);
    EXPECT_NE(named.find("+0x"), std::string::npos);
    EXPECT_EQ(named.find("missing_sa_siginfo"), std::string::npos);
    EXPECT_EQ(named.find("SIGSEGV=ddtrace"), std::string::npos);
    EXPECT_EQ(named.find("SIGSEGV=SIG_DFL"), std::string::npos);

    struct sigaction one_arg
    {};
    one_arg.sa_handler = foreign_one_arg_handler;
    sigemptyset(&one_arg.sa_mask);
    one_arg.sa_flags = 0;
    ASSERT_EQ(sigaction(SIGSEGV, &one_arg, nullptr), 0);
    ASSERT_EQ(sigaction(SIGBUS, &one_arg, nullptr), 0);

    const std::string one = describe_segv_handler_owners();
    EXPECT_NE(one.find("test_alt_stack_ownership"), std::string::npos);
    EXPECT_NE(one.find("+missing_sa_siginfo"), std::string::npos);
    EXPECT_EQ(one.find("SIGSEGV=ddtrace"), std::string::npos);
}
