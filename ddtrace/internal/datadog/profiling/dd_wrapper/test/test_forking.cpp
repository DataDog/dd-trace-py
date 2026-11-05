#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <mutex>
#include <numeric>
#include <pthread.h>
#include <utility>

struct UploadThreadState
{
    std::mutex mutex;
    std::condition_variable condition;
    bool started = false;
    bool finished = false;
};

// Initiate an upload in a separate thread, otherwise we won't be mid-upload during fork
void*
upload_in_thread(void* arg)
{
    auto* state = static_cast<UploadThreadState*>(arg);
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        state->started = true;
    }
    state->condition.notify_one();

    ddup_upload();

    std::unique_lock<std::mutex> lock(state->mutex);
    state->finished = true;
    std::notify_all_at_thread_exit(state->condition, std::move(lock));
    return nullptr;
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
    join_samplers(new_threads, done);
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
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);
    std::atomic<bool> done(false);
    std::vector<unsigned int> ids(num_threads);
    std::iota(ids.begin(), ids.end(), 0);
    std::vector<std::thread> samplers;
    launch_samplers(ids, sleep_time_ns, samplers, done);
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    // Finish samplers before the fork so the child does not inherit their in-flight allocations.
    join_samplers(samplers, done);
    done.store(false);

    UploadThreadState state;
    pthread_t thread;
    if (pthread_create(&thread, nullptr, upload_in_thread, &state) != 0) {
        std::cerr << "failed to create upload thread" << std::endl;
        std::exit(1);
    }

#ifndef DDUP_LEAK_CHECK
    // Detaching avoids a TSan leak report in the child.
    if (pthread_detach(thread) != 0) {
        std::cerr << "failed to detach upload thread" << std::endl;
        std::_Exit(1);
    }
#endif

    {
        std::unique_lock<std::mutex> lock(state.mutex);
        if (!state.condition.wait_for(lock, std::chrono::seconds(30), [&] { return state.started; })) {
            std::cerr << "upload thread did not start" << std::endl;
            std::_Exit(1);
        }
    }
    auto wait_for_upload = [&state] {
        std::unique_lock<std::mutex> lock(state.mutex);
        if (!state.condition.wait_for(lock, std::chrono::seconds(30), [&] { return state.finished; })) {
            std::cerr << "upload thread did not finish" << std::endl;
            std::_Exit(1);
        }
    };

#ifdef DDUP_LEAK_CHECK
    // The child inherits TLS allocations from other threads, which leak checkers report as lost.
    // Other builds allow the fork to overlap the upload.
    wait_for_upload();
    if (pthread_join(thread, nullptr) != 0) {
        std::cerr << "failed to join upload thread" << std::endl;
        std::_Exit(1);
    }
#endif

    // Fork, wait in the parent for child to finish
    pid_t pid = fork();
    if (pid < 0) {
        std::cerr << "fork failed" << std::endl;
        std::_Exit(1);
    }
    if (pid == 0) {
        // Child
        profile_in_child(num_threads, 500e3, done); // Child profiles for 500 microseconds
    }

    // Parent
    int status;
    done.store(true);
    if (waitpid(pid, &status, 0) != pid) {
        std::cerr << "waitpid failed" << std::endl;
        std::_Exit(1);
    }
    wait_for_upload();
    ddup_upload();
    if (!is_exit_normal(status)) {
        std::exit(1);
    }
    std::exit(0);
}

TEST(ForkDeathTest, SampleInThreadsAndForkNormal)
{
    EXPECT_EXIT(sample_in_threads_and_fork(4, 10e3), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, SampleInThreadsAndForkMany)
{
    EXPECT_EXIT(sample_in_threads_and_fork(16, 10e3), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, SampleInThreadsAndForkManyFast)
{
    EXPECT_EXIT(sample_in_threads_and_fork(16, 100), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, ForkStressTest4Thread)
{
    EXPECT_EXIT(sample_in_threads_and_fork(4, 100), ::testing::ExitedWithCode(0), ".*");
}

TEST(ForkDeathTest, ForkStressTest16Thread)
{
    EXPECT_EXIT(sample_in_threads_and_fork(16, 100), ::testing::ExitedWithCode(0), ".*");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
