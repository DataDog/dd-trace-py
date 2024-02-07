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
// This is a pretty complicated test, but basically we just want to validate that everything works when we fork
// 1. Startup two samplers in two threads
// 2. Wait for a second
// 3. Fork
// 4. In the child, startup two samplers in two threads
// 5. Wait for a second
// 6. Upload in the child
// 7. Exit
std::atomic<bool> done(false);
void
emulate_sampler()
{
    // Sends a sample at 100hz
    while (true) {
        send_sample();
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        if (done.load()) {
            std::cerr << "emulate_sampler done: " << std::this_thread::get_id() << std::endl;
            return;
        }
    }
}

void
sample_in_2threads_and_fork()
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

    // Creates two threads
    done.store(false);
    std::thread t1(emulate_sampler);
    std::thread t2(emulate_sampler);
    std::this_thread::sleep_for(std::chrono::seconds(1));

    // Fork, wait in the parent for child to finish
    pid_t pid = fork();
    if (pid == 0) {
        // Child
        std::this_thread::yield(); // Make sure parent can sit on wait
        std::thread s1(emulate_sampler);
        std::thread s2(emulate_sampler);
        std::this_thread::sleep_for(std::chrono::seconds(1));
        done.store(true);
        ddup_upload();
        ddup_cleanup();
        std::exit(0);
    } else {
        // Parent
        int status;
        done.store(true);
        waitpid(pid, &status, 0);
    }
    ddup_upload();
    ddup_cleanup();

    std::exit(0);
}

TEST(ForkDeathTest, SampleIn2ThreadsAndFork)
{
    // Same memory leak as before--whatever
    EXPECT_EXIT(sample_in_2threads_and_fork(), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
