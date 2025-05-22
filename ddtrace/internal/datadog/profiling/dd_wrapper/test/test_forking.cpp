#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <chrono>
#include <pthread.h>

// Initiate an upload in a separate thread, otherwise we won't be mid-upload during fork
void*
upload_in_thread(void*)
{
    ddup_upload(nullptr);

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
    ddup_upload(nullptr);
    std::exit(0);
}

bool
is_exit_normal(int status)
{
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

struct EmulateSamplerArg
{
    unsigned int id;
    unsigned int sleep_time_ns;
    std::atomic<bool>* done;
};

void*
emulate_sampler_wrapper(void* argp)
{
    EmulateSamplerArg* arg = reinterpret_cast<EmulateSamplerArg*>(argp);

    emulate_sampler(arg->id, arg->sleep_time_ns, *arg->done);

    return nullptr;
}

void
launch_pthread_samplers(std::vector<EmulateSamplerArg>& args, std::vector<pthread_t>& pthread_handles)
{
    for (unsigned int i = 0; i < args.size(); i++) {
        pthread_create(&pthread_handles[i], nullptr, emulate_sampler_wrapper, reinterpret_cast<void*>(&args[i]));
    }
}

void
join_pthread_samplers(std::vector<pthread_t>& threads, std::atomic<bool>& done)
{
    done.store(true);
    for (auto& handle : threads) {
        pthread_join(handle, nullptr);
    }
}

// Validates that sampling/uploads work around forks
void
sample_in_threads_and_fork(unsigned int num_threads, unsigned int sleep_time_ns)
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);
    std::atomic<bool> done(false);
    std::vector<pthread_t> thread_handles;
    std::vector<unsigned int> ids;
    std::vector<EmulateSamplerArg> args;

    for (unsigned int i = 0; i < ids.size(); i++) {
        auto id = ids[i];
        args.push_back(EmulateSamplerArg{ id, sleep_time_ns, &done });
    }

    // ddup is configured, launch threads
    launch_pthread_samplers(args, thread_handles);

    // Collect some profiling data for a few ms, then upload in a thread before forking
    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    pthread_t thread;
    if (pthread_create(&thread, nullptr, upload_in_thread, nullptr) != 0) {
        std::cout << "failed to create thread" << std::endl;
        std::exit(1);
    }

    // Fork, wait in the parent for child to finish
    pid_t pid = fork();
    if (pid == 0) {
        // Child
        profile_in_child(num_threads, 500e3, done); // Child profiles for 500ms
    }

    pthread_join(thread, nullptr);

    // Parent
    int status;
    done.store(true);
    waitpid(pid, &status, 0);
    ddup_upload(nullptr);
    join_pthread_samplers(thread_handles, done);
    if (!is_exit_normal(status)) {
        std::exit(1);
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
