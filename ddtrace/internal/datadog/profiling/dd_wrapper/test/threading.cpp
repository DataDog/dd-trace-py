#include "interface.hpp"
#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <thread>

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678
void
send_sample()
{
    ddup_start_sample();
    ddup_push_walltime(1.0, 1);

    for (int i = 0; i < 10; i++) {
        std::string filen = "file" + std::to_string(i);
        std::string funcn = "function" + std::to_string(i);
        ddup_push_frame("function", "file", 1, 1);
    }
    ddup_flush_sample();
}

// NB this global should only be toggled from within a death test,
// which will ensure it is done in a separate process.  Otherwise it would
// poison global state.
std::atomic<bool> done(false);
void
emulate_sampler()
{
    // Sends a sample at 100hz
    while (true) {
        send_sample();
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        if (done.load())
            break;
    }
}

void
sample_in_1thread()
{
    ddup_config_service("my_test_service");
    ddup_config_env("my_test_env");
    ddup_config_version("0.0.1");
    ddup_config_url("https://localhost:8126");
    ddup_config_runtime("cpython");
    ddup_config_runtime_version("3.10.6");
    ddup_config_profiler_version("3.100");
    ddup_config_max_nframes(256);
    ddup_init();

    // Creates one thread, waits for 1 second, then uploads
    std::thread t(emulate_sampler);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    ddup_upload();

    std::exit(0);
}

TEST(ThreadingDeathTest, SampleIn1Thread)
{
    EXPECT_EXIT(sample_in_1thread(), ::testing::ExitedWithCode(0), "");
}

void
sample_in_2threads()
{
    ddup_config_service("my_test_service");
    ddup_config_env("my_test_env");
    ddup_config_version("0.0.1");
    ddup_config_url("https://localhost:8126");
    ddup_config_runtime("cpython");
    ddup_config_runtime_version("3.10.6");
    ddup_config_profiler_version("3.100");
    ddup_config_max_nframes(256);
    ddup_init();

    // Creates two threads, waits for 1 second, then uploads
    done.store(false);
    std::thread t1(emulate_sampler);
    std::thread t2(emulate_sampler);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    done.store(true);
    std::this_thread::yield(); // Let threads finish
    ddup_upload();             // upload will fail right away, no need to wait

    // Do some cleanup.  This should probably prevent our memory leak, but
    // it doesn't.
    // TODO fix this
    ddup_cleanup();

    std::exit(0);
}

TEST(ThreadingDeathTest, SampleIn2Threads)
{
    // Currently we have a memory leak here.  It's about 2 pages and it appears to
    // be a fixed cost per thread.
    // TODO #SERIOUS fix this
    EXPECT_EXIT(sample_in_2threads(), ::testing::ExitedWithCode(0), "");
}

void
sample_in_4threads()
{
    ddup_config_service("my_test_service");
    ddup_config_env("my_test_env");
    ddup_config_version("0.0.1");
    ddup_config_url("https://localhost:8126");
    ddup_config_runtime("cpython");
    ddup_config_runtime_version("3.10.6");
    ddup_config_profiler_version("3.100");
    ddup_config_max_nframes(256);
    ddup_init();

    // Creates two threads, waits for 1 second, then uploads
    done.store(false);
    std::thread t1(emulate_sampler);
    std::thread t2(emulate_sampler);
    std::thread t3(emulate_sampler);
    std::thread t4(emulate_sampler);
    std::this_thread::sleep_for(std::chrono::seconds(1));
    done.store(true);
    std::this_thread::yield(); // Let threads finish
    ddup_upload();             // upload will fail right away, no need to wait

    // Do some cleanup.  This should probably prevent our memory leak, but
    // it doesn't.
    // TODO fix this
    ddup_cleanup();

    std::exit(0);
}

TEST(ThreadingDeathTest, SampleIn4Threads)
{
    // This is a test to quickly verify the scaling properties of the leak
    EXPECT_EXIT(sample_in_4threads(), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
