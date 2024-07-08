#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <chrono>

// Initiate an upload in a separate thread, otherwise we won't be mid-upload during fork
void
upload_in_thread()
{
    std::thread([&]() { ddup_upload(); }).detach();
}

[[noreturn]] void
profile_in_child(unsigned int num_threads, unsigned int run_time_ns, std::atomic<bool>& done)
{
    // Assumes setup has been called. Launch some samplers, wait, upload, exit.
    // Managing interleaved execution is tricky, so the fork exits--rather than returns--when it is done.
    std::vector<unsigned int> ids;
    for (unsigned int i = 0; i < num_threads; i++) {
        ids.push_back(i);
    }
    std::vector<std::thread> new_threads;
    launch_samplers(ids, 10e3, new_threads, done);
    std::this_thread::sleep_for(std::chrono::nanoseconds(run_time_ns));
    done.store(true);
    ddup_upload();
    std::exit(0);
}

bool
is_exit_normal(int status)
{
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

// Validates that sampling/uploads work around forks
void
sample_in_threads_and_fork(unsigned int num_threads, unsigned int sleep_time_ns)
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126", "cpython", "3.10.6", "3.100", 256);
    std::atomic<bool> done(false);
    std::vector<std::thread> threads;
    std::vector<unsigned int> ids;
    for (unsigned int i = 0; i < num_threads; i++) {
        ids.push_back(i);
    }

    // ddup is configured, launch threads
    launch_samplers(ids, sleep_time_ns, threads, done);

    // Collect some profiling data for a few ms, then upload in a thread before forking
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    upload_in_thread();

    // Fork, wait in the parent for child to finish
    pid_t pid = fork();
    if (pid == 0) {
        // Child
        profile_in_child(num_threads, 500e3, done); // Child profiles for 500ms
    }

    // Parent
    int status;
    done.store(true);
    waitpid(pid, &status, 0);
    upload_in_thread();
    if (!is_exit_normal(status)) {
        std::exit(1);
    }
    std::exit(0);
}

// Really try to break things with many forks
void
fork_stress_test(unsigned int num_threads, unsigned int sleep_time_ns, unsigned int num_children)
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126", "cpython", "3.10.6", "3.100", 256);
    std::atomic<bool> done(false);
    std::vector<std::thread> threads;
    std::vector<unsigned int> ids;
    for (unsigned int i = 0; i < num_threads; i++) {
        ids.push_back(i);
    }

    // ddup is configured, launch threads
    launch_samplers(ids, sleep_time_ns, threads, done);

    std::vector<pid_t> children;
    upload_in_thread();
    while (num_children > 0) {
        pid_t pid = fork();
        if (pid == 0) {
            // Child
            profile_in_child(num_threads, 500e3, done); // Child profiles for 500ms
        }
        children.push_back(pid);
        num_children--;
    }

    // Parent
    int status;
    done.store(true);
    upload_in_thread();
    for (pid_t pid : children) {
        waitpid(pid, &status, 0);
        if (!is_exit_normal(status)) {
            std::exit(1);
        }
    }
    std::exit(0);
}

TEST(ForkDeathTest, SampleInThreadsAndForkNormal)
{
    // Same memory leak as before--whatever
    EXPECT_EXIT(sample_in_threads_and_fork(4, 10e3), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, SampleInThreadsAndForkMany)
{
    // Same memory leak as before--whatever
    EXPECT_EXIT(sample_in_threads_and_fork(16, 10e3), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, SampleInThreadsAndForkManyFast)
{
    // Same memory leak as before--whatever
    EXPECT_EXIT(sample_in_threads_and_fork(16, 100), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, ForkStressTest4Thread)
{
    // Same memory leak as before--whatever
    EXPECT_EXIT(sample_in_threads_and_fork(4, 100), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, ForkStressTest16Thread)
{
    // Same memory leak as before--whatever
    EXPECT_EXIT(sample_in_threads_and_fork(16, 100), ::testing::ExitedWithCode(0), ".*");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
