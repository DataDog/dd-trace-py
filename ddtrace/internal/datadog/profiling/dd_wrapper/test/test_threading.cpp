#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <chrono>

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678
void
generic_launch_sleep_upload(int n, unsigned int sleep_time_ns)
{
    std::atomic<bool> done{ false };
    std::vector<std::thread> threads;
    std::vector<unsigned int> ids;
    for (int i = 0; i < n; i++)
        ids.push_back(i);
    launch_samplers(ids, sleep_time_ns, threads, done);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    join_samplers(threads, done);
    ddup_upload(); // upload will fail right away, no need to wait
}

void
emulate_profiler(unsigned int num_threads, unsigned int sample_ns)
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://localhost:8126", "cpython", "3.10.6", "3.100", 256);
    generic_launch_sleep_upload(num_threads, sample_ns);

    // Assumed to execute within a thread
    std::exit(0);
}

TEST(ThreadingDeathTest, SampleIn1Thread)
{
    EXPECT_EXIT(emulate_profiler(1, 10e3), ::testing::ExitedWithCode(0), "");
}

TEST(ThreadingDeathTest, SampleIn2Threads)
{
    // Currently we have a memory leak here.  It's about 2 pages and it appears to
    // be a fixed cost per thread.
    // TODO #SERIOUS fix this
    EXPECT_EXIT(emulate_profiler(2, 10e3), ::testing::ExitedWithCode(0), "");
}

TEST(ThreadingDeathTest, SampleIn4Threads)
{
    // This is a test to quickly verify the scaling properties of the leak
    EXPECT_EXIT(emulate_profiler(4, 10e3), ::testing::ExitedWithCode(0), "");
}

TEST(ThreadingDeathTest, SampleIn8Threads)
{
    // This is a test to quickly verify the scaling properties of the leak
    EXPECT_EXIT(emulate_profiler(8, 10e3), ::testing::ExitedWithCode(0), "");
}

TEST(ThreadingDeathTest, SampleIn16Threads)
{
    // This is a test to quickly verify the scaling properties of the leak
    EXPECT_EXIT(emulate_profiler(16, 10e3), ::testing::ExitedWithCode(0), "");
}

TEST(ThreadingDeathTest, SampleIn4ThreadsFast)
{
    // Same as before, but sample once per 0.1 ms
    EXPECT_EXIT(emulate_profiler(4, 100), ::testing::ExitedWithCode(0), "");
}

TEST(ThreadingDeathTest, SampleIn8ThreadsFast)
{
    // Same as before, but sample once per 0.1 ms
    EXPECT_EXIT(emulate_profiler(8, 100), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
