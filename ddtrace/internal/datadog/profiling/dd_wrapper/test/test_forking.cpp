#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <chrono>
#include <iostream>
#include <pthread.h>
#include <unistd.h>

#if defined(__has_feature)
#if __has_feature(address_sanitizer)
#include <sanitizer/lsan_interface.h>
#define HAS_LSAN 1
#else
void
__lsan_disable()
{
}
void
__lsan_enable()
{
}
#endif
#else
void
__lsan_disable()
{
}
void
__lsan_enable()
{
}
#endif

// Debug helper to print with timestamp and flush
void
dbg(const char* msg, int extra = -1)
{
    auto now = std::chrono::steady_clock::now().time_since_epoch();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
    if (extra >= 0) {
        std::cerr << "[DBG " << ms << "ms pid=" << getpid() << " tid=" << gettid() << "] " << msg << " " << extra
                  << std::endl;
    } else {
        std::cerr << "[DBG " << ms << "ms pid=" << getpid() << " tid=" << gettid() << "] " << msg << std::endl;
    }
}

// Initiate an upload in a separate thread, otherwise we won't be mid-upload during fork
void*
upload_in_thread(void*)
{
    dbg("upload_in_thread: start");
    ddup_upload();
    dbg("upload_in_thread: done");

    return nullptr;
}

[[noreturn]] void
profile_in_child(unsigned int num_threads, unsigned int run_time_ns, std::atomic<bool>& done)
{
    dbg("profile_in_child: start", static_cast<int>(num_threads));
    // Assumes setup has been called. Launch some samplers, wait, upload, exit.
    // Managing interleaved execution is tricky, so the fork exits--rather than returns--when it is done.
    std::vector<unsigned int> ids;
    for (unsigned int i = 0; i < num_threads; i++) {
        ids.push_back(i);
    }
    std::vector<std::thread> new_threads;
    dbg("profile_in_child: launching samplers");
    launch_samplers(ids, 10e3, new_threads, done);
    dbg("profile_in_child: sleeping");
    std::this_thread::sleep_for(std::chrono::nanoseconds(run_time_ns));
    dbg("profile_in_child: setting done=true");
    done.store(true);
    dbg("profile_in_child: joining samplers");
    join_samplers(new_threads, done);
    dbg("profile_in_child: uploading");
    ddup_upload();
    dbg("profile_in_child: exiting");
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
    dbg("sample_in_threads_and_fork: start", static_cast<int>(num_threads));
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);
    std::atomic<bool> done(false);
    std::vector<pthread_t> thread_handles(num_threads);
    std::vector<unsigned int> ids;
    std::vector<EmulateSamplerArg> args;

    for (unsigned int i = 0; i < num_threads; i++) {
        ids.push_back(i);
    }

    for (unsigned int i = 0; i < ids.size(); i++) {
        auto id = ids[i];
        args.push_back(EmulateSamplerArg{ id, sleep_time_ns, &done });
    }

    // ddup is configured, launch threads
    dbg("sample_in_threads_and_fork: launching pthread samplers");
    launch_pthread_samplers(args, thread_handles);

    // Collect some profiling data for a few ms, then upload in a thread before forking
    dbg("sample_in_threads_and_fork: sleeping 500ms");
    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    dbg("sample_in_threads_and_fork: creating upload thread");
    pthread_t thread;
    if (pthread_create(&thread, nullptr, upload_in_thread, nullptr) != 0) {
        std::cout << "failed to create thread" << std::endl;
        std::exit(1);
    }

    // Fork, wait in the parent for child to finish
    dbg("sample_in_threads_and_fork: about to fork");
    pid_t pid = fork();
    if (pid == 0) {
        // Child - disable LSan because we inherit orphaned thread-local allocations
        // from parent threads that don't exist in the child (expected with fork+threads)
        __lsan_disable();
        dbg("sample_in_threads_and_fork: in child after fork");
        profile_in_child(num_threads, 500e3, done); // Child profiles for 500ms
    }

    dbg("sample_in_threads_and_fork: parent - joining upload thread");
    pthread_join(thread, nullptr);
    dbg("sample_in_threads_and_fork: parent - upload thread joined");

    // Parent
    int status;
    done.store(true);
    dbg("sample_in_threads_and_fork: parent - waiting for child", pid);
    waitpid(pid, &status, 0);
    dbg("sample_in_threads_and_fork: parent - child exited", WEXITSTATUS(status));
    dbg("sample_in_threads_and_fork: parent - uploading");
    ddup_upload();
    dbg("sample_in_threads_and_fork: parent - joining pthread samplers");
    join_pthread_samplers(thread_handles, done);
    dbg("sample_in_threads_and_fork: parent - done");
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
