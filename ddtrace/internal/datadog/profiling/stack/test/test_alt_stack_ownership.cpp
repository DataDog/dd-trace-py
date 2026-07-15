#include "echion/danger.h"

#include <gtest/gtest.h>

#include <csignal>
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
