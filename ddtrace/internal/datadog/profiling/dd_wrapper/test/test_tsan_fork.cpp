#include "ddup_interface.hpp"
#include "test_utils.hpp"

#include <gtest/gtest.h>
#include <sys/wait.h>
#include <unistd.h>

// Verify that postfork_child() correctly unlocks all mutexes and reinitializes
// profiler state without triggering TSan warnings. The child is single-threaded
// after fork, so postfork_child() unlocks everything first, then reinitializes
// without holding any locks.
TEST(TSanFork, DictionaryMutexNotDoubleLocked)
{
    configure("test", "test", "0.1", "http://localhost:8126", "python", "3.12", "1.0.0", 64);

    pid_t pid = fork();
    ASSERT_NE(pid, -1) << "fork failed";

    if (pid == 0) {
        // Child: postfork handlers have already run.
        // If the double-lock bug exists, TSan already reported it.
        // Try a sample to exercise the reinitialized dictionary.
        send_sample(1);
        _exit(0);
    }

    // Parent: wait for child
    int status = 0;
    waitpid(pid, &status, 0);
    EXPECT_TRUE(WIFEXITED(status)) << "child did not exit normally";
    EXPECT_EQ(WEXITSTATUS(status), 0) << "child exited with error";
}
