#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <pthread.h>
#include <thread>
#include <vector>

// Test for race condition between threads using ProfilesDictionary and cleanup
// The scenario:
// 1. Multiple threads are actively calling intern_string/push_frame
// 2. Main thread calls ddup_cleanup which releases the ProfilesDictionary
// 3. Worker threads may still be mid-operation when dictionary is freed -> SEGFAULT

struct SamplerArg
{
    unsigned int id;
    unsigned int sleep_time_ns;
    std::atomic<bool>* done;
};

void*
sampler_wrapper(void* argp)
{
    SamplerArg* arg = reinterpret_cast<SamplerArg*>(argp);
    emulate_sampler(arg->id, arg->sleep_time_ns, *arg->done);
    return nullptr;
}

void
launch_pthread_samplers(std::vector<SamplerArg>& args, std::vector<pthread_t>& handles)
{
    for (size_t i = 0; i < args.size(); i++) {
        pthread_create(&handles[i], nullptr, sampler_wrapper, reinterpret_cast<void*>(&args[i]));
    }
}

void
detach_pthread_samplers(std::vector<pthread_t>& handles)
{
    for (auto& handle : handles) {
        pthread_detach(handle);
    }
}

void
join_pthread_samplers(std::vector<pthread_t>& handles)
{
    for (auto& handle : handles) {
        pthread_join(handle, nullptr);
    }
}

// Stress test: cleanup while threads are actively sampling
// This simulates the atexit race condition where:
// - Python atexit calls profiler.stop() which increments seq_num but doesn't join
// - C++ atexit calls ddup_cleanup() while sampling thread is still running
//
// IMPORTANT: After ddup_cleanup(), the threads will likely crash when they
// try to use the freed ProfilesDictionary. This is the race we're testing.
void
cleanup_while_sampling(unsigned int num_threads, unsigned int run_time_ms, unsigned int sleep_time_ns)
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);

    std::atomic<bool> done(false);
    std::vector<pthread_t> thread_handles(num_threads);
    std::vector<SamplerArg> args;

    for (unsigned int i = 0; i < num_threads; i++) {
        args.push_back(SamplerArg{ i % 4 + 1, sleep_time_ns, &done });
    }

    // Launch sampling threads
    launch_pthread_samplers(args, thread_handles);

    // Let threads run for a while to build up activity
    std::this_thread::sleep_for(std::chrono::milliseconds(run_time_ms));

    // NOW THE RACE: call cleanup while threads are still running!
    // This simulates what happens during exit when atexit handlers run
    // but threads haven't been properly stopped/joined.
    //
    // NOTE: We detach the threads because after cleanup, they may crash
    // and we don't want to hang on join. The death test framework will
    // catch any crashes.
    detach_pthread_samplers(thread_handles);

    ddup_cleanup();

    // Signal threads to stop (but cleanup already happened!)
    done.store(true);

    // Give threads a moment to potentially crash
    std::this_thread::sleep_for(std::chrono::milliseconds(10));

    std::exit(0);
}

// Variant: cleanup with very short delay to maximize race window
void
cleanup_immediately_while_sampling(unsigned int num_threads)
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);

    std::atomic<bool> done(false);
    std::vector<pthread_t> thread_handles(num_threads);
    std::vector<SamplerArg> args;

    for (unsigned int i = 0; i < num_threads; i++) {
        // Very fast sampling (100ns) to maximize chance of being mid-operation
        args.push_back(SamplerArg{ i % 4 + 1, 100, &done });
    }

    launch_pthread_samplers(args, thread_handles);

    // Minimal delay - just enough to let threads start
    std::this_thread::sleep_for(std::chrono::microseconds(100));

    // Detach before cleanup since threads may crash after
    detach_pthread_samplers(thread_handles);

    // Cleanup immediately while threads are definitely still running
    ddup_cleanup();

    done.store(true);

    // Give threads a moment to potentially crash
    std::this_thread::sleep_for(std::chrono::milliseconds(10));

    std::exit(0);
}

// Variant: proper shutdown (join threads BEFORE cleanup)
// This should always pass and serves as a control test
void
proper_shutdown(unsigned int num_threads, unsigned int run_time_ms, unsigned int sleep_time_ns)
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);

    std::atomic<bool> done(false);
    std::vector<pthread_t> thread_handles(num_threads);
    std::vector<SamplerArg> args;

    for (unsigned int i = 0; i < num_threads; i++) {
        args.push_back(SamplerArg{ i % 4 + 1, sleep_time_ns, &done });
    }

    launch_pthread_samplers(args, thread_handles);

    std::this_thread::sleep_for(std::chrono::milliseconds(run_time_ms));

    // CORRECT ORDER: signal stop, join threads, THEN cleanup
    done.store(true);
    join_pthread_samplers(thread_handles);

    ddup_cleanup();

    std::exit(0);
}

// Tests using death tests to detect crashes/segfaults
// Note: The "cleanup while sampling" tests may crash due to the race condition.
// If they pass (exit 0), the race didn't manifest in that run.
// If they crash, we've demonstrated the bug.

// Control tests - these should always pass
TEST(CleanupRaceDeathTest, ProperShutdown4Threads)
{
    EXPECT_EXIT(proper_shutdown(4, 50, 1000), ::testing::ExitedWithCode(0), ".*");
}

TEST(CleanupRaceDeathTest, ProperShutdown16Threads)
{
    EXPECT_EXIT(proper_shutdown(16, 50, 1000), ::testing::ExitedWithCode(0), ".*");
}

// // Race condition tests - may crash if race manifests
// TEST(CleanupRaceDeathTest, CleanupWhileSampling4Threads)
// {
//     // 4 threads, 50ms run, 1000ns sleep between samples
//     EXPECT_EXIT(cleanup_while_sampling(4, 50, 1000), ::testing::ExitedWithCode(0), ".*");
// }

// TEST(CleanupRaceDeathTest, CleanupWhileSampling16Threads)
// {
//     // More threads = more contention
//     EXPECT_EXIT(cleanup_while_sampling(16, 50, 1000), ::testing::ExitedWithCode(0), ".*");
// }

// TEST(CleanupRaceDeathTest, CleanupWhileSamplingFast)
// {
//     // Very fast sampling to maximize race window
//     EXPECT_EXIT(cleanup_while_sampling(8, 50, 100), ::testing::ExitedWithCode(0), ".*");
// }

// TEST(CleanupRaceDeathTest, CleanupImmediately4Threads)
// {
//     EXPECT_EXIT(cleanup_immediately_while_sampling(4), ::testing::ExitedWithCode(0), ".*");
// }

// TEST(CleanupRaceDeathTest, CleanupImmediately16Threads)
// {
//     EXPECT_EXIT(cleanup_immediately_while_sampling(16), ::testing::ExitedWithCode(0), ".*");
// }

// // Stress tests with many threads and aggressive timing
// TEST(CleanupRaceDeathTest, StressTest32ThreadsFast)
// {
//     EXPECT_EXIT(cleanup_while_sampling(32, 100, 50), ::testing::ExitedWithCode(0), ".*");
// }

// TEST(CleanupRaceDeathTest, StressTest64ThreadsFast)
// {
//     EXPECT_EXIT(cleanup_while_sampling(64, 100, 50), ::testing::ExitedWithCode(0), ".*");
// }

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
