#include "ddup_interface.hpp"
#include "test_utils.hpp"

#include <gtest/gtest.h>
#include <sys/wait.h>
#include <unistd.h>

// Minimal reproducer for the TSan double-lock warning on profiles_dictionary_mtx
// after fork. The sequence that triggers it:
//   1. Parent: prefork() locks profiles_dictionary_mtx
//   2. fork() — child inherits the locked mutex + TSan shadow state
//   3. Child: postfork_child() placement-news the mutex (TSan doesn't see reset)
//   4. Child: release_profiles_dictionary() tries to lock → TSan: double lock
//
// The fix: avoid locking in postfork_child (child is single-threaded).
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
