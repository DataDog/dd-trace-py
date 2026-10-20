#include "code_provenance.hpp"
#include "code_provenance_interface.hpp"
#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include "uploader.hpp"
#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <pthread.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

namespace {
std::atomic<bool> fork_prepare_entered{ false };
std::atomic<bool> fork_prepare_continue{ false };

void
pause_fork_prepare()
{
    fork_prepare_entered.store(true);
    while (!fork_prepare_continue.load()) {
        std::this_thread::yield();
    }
}
}

TEST(CodeProvenanceTest, SnapshotSurvivesUpdate)
{
    auto& provenance = Datadog::CodeProvenance::get_instance();
    provenance.set_json_str("original");
    auto snapshot = provenance.get_json_snapshot();
    std::thread updater([&provenance] { provenance.set_json_str("modified"); });
    updater.join();
    EXPECT_EQ(std::string(snapshot), "original");
}

void
fork_before_profiler_start()
{
    alarm(20);
    auto& provenance = Datadog::CodeProvenance::get_instance();
    std::atomic<bool> locked{ false };
    std::atomic<bool> finished{ false };
    std::thread([&] {
        provenance.prefork();
        locked.store(true);
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        provenance.postfork_parent();
        finished.store(true);
    }).detach();
    while (!locked.load()) {
        std::this_thread::yield();
    }

    pid_t pid = fork();
    if (pid == 0) {
        alarm(3);
        code_provenance_set_json_str("child");
        auto sample = ddup_start_sample();
        ddup_drop_sample(sample);
        std::_Exit(0);
    }
    if (pid < 0) {
        std::_Exit(1);
    }
    while (!finished.load()) {
        std::this_thread::yield();
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        std::_Exit(1);
    }
    std::_Exit(0);
}

TEST(CodeProvenanceDeathTest, ForkBeforeProfilerStart)
{
    EXPECT_EXIT(fork_before_profiler_start(), ::testing::ExitedWithCode(0), "");
}

void
fork_during_profiler_start()
{
    alarm(20);
    if (pthread_atfork(pause_fork_prepare, nullptr, nullptr) != 0) {
        std::_Exit(1);
    }

    std::atomic<bool> fork_finished{ false };
    std::atomic<bool> child_succeeded{ false };
    std::thread forker([&] {
        pid_t pid = fork();
        if (pid == 0) {
            std::_Exit(0);
        }
        int status = 0;
        child_succeeded.store(pid > 0 && waitpid(pid, &status, 0) == pid && WIFEXITED(status) &&
                              WEXITSTATUS(status) == 0);
        fork_finished.store(true);
    });
    while (!fork_prepare_entered.load()) {
        std::this_thread::yield();
    }

    ddup_start();
    Datadog::Uploader::lock();
    fork_prepare_continue.store(true);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(500);
    while (!fork_finished.load() && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const bool crossed_upload = fork_finished.load();
    Datadog::Uploader::unlock();
    forker.join();
    std::_Exit(!crossed_upload && child_succeeded.load() ? 0 : 1);
}

TEST(UploadDeathTest, ForkStartedBeforeProfilerInitialization)
{
    EXPECT_EXIT(fork_during_profiler_start(), ::testing::ExitedWithCode(0), "");
}

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678

void
single_sample_noframe()
{

    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);

    // Collect and flush one sample
    auto h = ddup_start_sample();
    ddup_push_walltime(h, 1.0, 1);
    ddup_flush_sample(h);
    ddup_drop_sample(h);
    h = nullptr;

    // Upload.  It'll fail, but whatever
    ddup_upload();

    std::exit(0);
}

TEST(UploadDeathTest, SingleSample)
{
    EXPECT_EXIT(single_sample_noframe(), ::testing::ExitedWithCode(0), "");
}

struct UploadOnThreadExit
{
    ~UploadOnThreadExit()
    {
        const bool uploaded = ddup_upload();
        std::cerr << (uploaded ? "thread-local upload attempted" : "thread-local upload skipped") << std::endl;
    }
};

void
upload_from_thread_local_destructor()
{
    configure("my_test_service", "my_test_env", "0.0.1", "http://127.0.0.1:1", "cpython", "3.10.6", "3.100", 256);
    thread_local UploadOnThreadExit upload_on_exit;
    (void)upload_on_exit;
    ddup_config_url("http://[");
    if (ddup_upload()) {
        std::exit(1);
    }
    ddup_config_url("http://127.0.0.1:1");
    ddup_upload();
    std::exit(0);
}

TEST(UploadDeathTest, UploadFromThreadLocalDestructor)
{
    EXPECT_EXIT(upload_from_thread_local_destructor(), ::testing::ExitedWithCode(0), "thread-local upload skipped");
}

void
upload_after_file_export()
{
    configure("my_test_service", "my_test_env", "0.0.1", "http://127.0.0.1:1", "cpython", "3.10.6", "3.100", 256);
    ddup_config_output_filename("/dev/null/profile");
    thread_local UploadOnThreadExit upload_on_exit;
    (void)upload_on_exit;
    ddup_upload();
    std::exit(0);
}

TEST(UploadDeathTest, UploadFromThreadLocalDestructorAfterFileExport)
{
    EXPECT_EXIT(upload_after_file_export(), ::testing::ExitedWithCode(0), "thread-local upload skipped");
}

void
upload_from_unwatched_exit_thread()
{
    std::atexit([] {
        ddup_upload();
        std::cerr << "atexit upload returned" << std::endl;
    });
    configure("my_test_service", "my_test_env", "0.0.1", "http://127.0.0.1:1", "cpython", "3.10.6", "3.100", 256);
    code_provenance_set_json_str(std::string(128 * 1024, 'x'));
    ddup_upload();
    std::thread([] { std::exit(0); }).join();
}

TEST(UploadDeathTest, UploadFromUnwatchedExitThread)
{
    EXPECT_EXIT(upload_from_unwatched_exit_thread(), ::testing::ExitedWithCode(0), "atexit upload returned");
}

void
single_oneframe_sample()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);

    // Collect and flush one sample with one frame
    auto h = ddup_start_sample();
    ddup_push_walltime(h, 1.0, 1);
    ddup_push_frame(h, "my_test_frame", "my_test_file", 1, 1);
    ddup_flush_sample(h);
    ddup_drop_sample(h);
    h = nullptr;

    // Upload.  It'll fail, but whatever
    ddup_upload();

    std::exit(0);
}

TEST(UploadDeathTest, SingleSampleOneFrame)
{
    EXPECT_EXIT(single_oneframe_sample(), ::testing::ExitedWithCode(0), "");
}

void
single_manyframes_sample()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 512);

    // Collect and flush one sample with one frame
    auto h = ddup_start_sample();
    ddup_push_walltime(h, 1.0, 1);

    // Populate the frames; we add exactly 512, which ought to be the limit
    std::string base_func = "my_function_";
    std::string base_file = "my_file_";
    for (int i = 0; i < 512; i++) {
        std::string name = base_func + std::to_string(i);
        std::string file = base_file + std::to_string(i);
        ddup_push_frame(h, name.c_str(), file.c_str(), 1, 1);
    }
    ddup_flush_sample(h);
    ddup_drop_sample(h);
    h = nullptr;

    // Upload.  It'll fail, but whatever
    ddup_upload();

    std::exit(0);
}

TEST(UploadDeathTest, SingleSampleManyFrames)
{
    EXPECT_EXIT(single_manyframes_sample(), ::testing::ExitedWithCode(0), "");
}

void
single_toomanyframes_sample()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 512);

    // Collect and flush one sample with one frame
    auto h = ddup_start_sample();
    ddup_push_walltime(h, 1.0, 1);

    // Now, for something completely different, we add way too many frames
    std::string base_func = "my_function_";
    std::string base_file = "my_file_";
    for (int i = 0; i < 1024; i++) {
        std::string name = base_func + std::to_string(i);
        std::string file = base_file + std::to_string(i);
        ddup_push_frame(h, name.c_str(), file.c_str(), 1, 1);
    }
    ddup_flush_sample(h);
    ddup_drop_sample(h);
    h = nullptr;

    // Upload.  It'll fail, but whatever
    ddup_upload();

    std::exit(0);
}

TEST(UploadDeathTest, SingleSampleTooManyFrames)
{
    EXPECT_EXIT(single_toomanyframes_sample(), ::testing::ExitedWithCode(0), "");
}

void
lotsa_frames_lotsa_samples()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 512);

    // 60 seconds @ 100 hertz
    for (int i = 0; i < 60 * 100; i++) {
        auto h = ddup_start_sample();
        ddup_push_cputime(h, 1.0, 1);
        ddup_push_walltime(h, 1.0, 1);
        ddup_push_exceptioninfo(h, "WowThisIsBad", 1);
        ddup_push_alloc(h, 100, 1);
        ddup_push_heap(h, 100);
        ddup_push_acquire(h, 66, 1);
        ddup_push_release(h, 66, 1);
        ddup_push_threadinfo(h, i + 1024, i * 200 % 11, "MyFavoriteThreadEver");
        ddup_push_task_id(h, i);
        ddup_push_task_name(h, "MyFavoriteTaskEver");

        // Now, for something completely different, we
        // add way too many frames
        std::string base_func = "my_function_";
        std::string base_file = "my_file_";
        for (int j = 0; j < 64; j++) {
            std::string name = base_func + std::to_string(j) + "." + std::to_string(i);
            std::string file = base_file + std::to_string(j) + "." + std::to_string(i);
            ddup_push_frame(h, name.c_str(), file.c_str(), 1, 1);
        }
        ddup_flush_sample(h);
        ddup_drop_sample(h);
        h = nullptr;
    }

    // Upload.  It'll fail, but whatever
    ddup_upload();

    std::exit(0);
}

TEST(UploadDeathTest, LotsaSamplesLotsaFrames)
{
    EXPECT_EXIT(lotsa_frames_lotsa_samples(), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
