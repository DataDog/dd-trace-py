#include "interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <chrono>

// NB this global should only be toggled from within a death test,
// which will ensure it is done in a separate process.  Otherwise it would
// poison global state.
// This is a pretty complicated test, but basically we just want to validate that everything works when we fork
// 1. Startup two samplers in two threads
// 2. Wait for a second
// 3. Fork
// 4. In the child, startup two samplers in two threads
// 5. Wait for a second
// 6. Upload in the child
// 7. Exit
void
sample_in_threads_and_fork()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126", "cpython", "3.10.6", "3.100", 256);

    // Creates two threads
    std::atomic<bool> done(false);
    std::vector<std::thread> threads;
    std::vector<unsigned int> ids{ 0, 1, 2, 3 };
    launch_samplers(ids, threads, done);

    // Fork, wait in the parent for child to finish
    pid_t pid = fork();
    if (pid == 0) {
        // Child
        // Reuse the ids; this is the typical case
        std::this_thread::yield(); // Make sure parent can sit on wait
        std::vector<std::thread> new_threads;
        launch_samplers(ids, new_threads, done);
        std::this_thread::sleep_for(std::chrono::seconds(1));
        done.store(true);
    } else {
        // Parent
        int status;
        done.store(true);
        waitpid(pid, &status, 0);
    }
    ddup_upload();
    std::exit(0);
}

TEST(ForkDeathTest, SampleInThreadsAndFork)
{
    // Same memory leak as before--whatever
    EXPECT_EXIT(sample_in_threads_and_fork(), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
