#include "interface.hpp"
#include <gtest/gtest.h>

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678

void
single_sample_noframe()
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

    // Collect and flush one sample
    ddup_start_sample();
    ddup_push_walltime(1.0, 1);
    ddup_flush_sample();

    // Upload.  It'll fail, but whatever
    ddup_upload();

    std::exit(0);
}

TEST(UploadDeathTest, SingleSample)
{
    EXPECT_EXIT(single_sample_noframe(), ::testing::ExitedWithCode(0), "");
}

void
single_oneframe_sample()
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

    // Collect and flush one sample with one frame
    ddup_start_sample();
    ddup_push_walltime(1.0, 1);
    ddup_push_frame("my_test_frame", "my_test_file", 1, 1);
    ddup_flush_sample();

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
    ddup_config_service("my_test_service");
    ddup_config_env("my_test_env");
    ddup_config_version("0.0.1");
    ddup_config_url("https://localhost:8126");
    ddup_config_runtime("cpython");
    ddup_config_runtime_version("3.10.6");
    ddup_config_profiler_version("3.100");
    ddup_config_max_nframes(512);
    ddup_init();

    // Collect and flush one sample with one frame
    ddup_start_sample();
    ddup_push_walltime(1.0, 1);

    // Populate the frames; we add exactly 512, which ought to
    // be the limit
    std::string base_func = "my_function_";
    std::string base_file = "my_file_";
    for (int i = 0; i < 1024; i++) {
        std::string name = base_func + std::to_string(i);
        std::string file = base_file + std::to_string(i);
        ddup_push_frame(name.c_str(), file.c_str(), 1, 1);
    }
    ddup_flush_sample();

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
    ddup_config_service("my_test_service");
    ddup_config_env("my_test_env");
    ddup_config_version("0.0.1");
    ddup_config_url("https://localhost:8126");
    ddup_config_runtime("cpython");
    ddup_config_runtime_version("3.10.6");
    ddup_config_profiler_version("3.100");
    ddup_config_max_nframes(512);
    ddup_init();

    // Collect and flush one sample with one frame
    ddup_start_sample();
    ddup_push_walltime(1.0, 1);

    // Now, for something completely different, we
    // add way too many frames
    std::string base_func = "my_function_";
    std::string base_file = "my_file_";
    for (int i = 0; i < 1024; i++) {
        std::string name = base_func + std::to_string(i);
        std::string file = base_file + std::to_string(i);
        ddup_push_frame(name.c_str(), file.c_str(), 1, 1);
    }
    ddup_flush_sample();

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
    ddup_config_service("my_test_service");
    ddup_config_env("my_test_env");
    ddup_config_version("0.0.1");
    ddup_config_url("https://localhost:8126");
    ddup_config_runtime("cpython");
    ddup_config_runtime_version("3.10.6");
    ddup_config_profiler_version("3.100");
    ddup_config_max_nframes(512);
    ddup_init();

    // 60 seconds @ 100 hertz
    for (int i = 0; i < 60 * 100; i++) {
        ddup_start_sample();
        ddup_push_cputime(1.0, 1);
        ddup_push_walltime(1.0, 1);
        ddup_push_exceptioninfo("WowThisIsBad", 1);
        ddup_push_alloc(100, 1);
        ddup_push_heap(100);
        ddup_push_acquire(66, 1);
        ddup_push_release(66, 1);
        ddup_push_threadinfo(i + 1024, i * 200 % 11, "MyFavoriteThreadEver");
        ddup_push_task_id(i);
        ddup_push_task_name("MyFavoriteTaskEver");

        // Now, for something completely different, we
        // add way too many frames
        std::string base_func = "my_function_";
        std::string base_file = "my_file_";
        for (int j = 0; j < 64; j++) {
            std::string name = base_func + std::to_string(j) + "." + std::to_string(i);
            std::string file = base_file + std::to_string(j) + "." + std::to_string(i);
            ddup_push_frame(name.c_str(), file.c_str(), 1, 1);
        }
        ddup_flush_sample();
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
