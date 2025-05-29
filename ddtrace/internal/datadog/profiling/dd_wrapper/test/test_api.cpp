#include "ddup_interface.hpp"
#include "test_utils.hpp"
#include "uploader_builder.hpp"
#include <gtest/gtest.h>

// NOTE: cmake gives us an old gtest, and rather than update I just use the
//       "workaround" in the following link
//       https://stackoverflow.com/a/71257678

void
single_sample_noframe()
{

    configure(256);

    // Collect and flush one sample
    auto h = ddup_start_sample();
    ddup_push_walltime(h, 1.0, 1);
    ddup_flush_sample(h);
    ddup_drop_sample(h);
    h = nullptr;

    std::unique_ptr<Datadog::UploaderConfig> config = std::make_unique<Datadog::UploaderConfig>();
    // Upload.  It'll fail, but whatever
    ddup_upload(std::move(config));

    std::exit(0);
}

TEST(UploadDeathTest, SingleSample)
{
    EXPECT_EXIT(single_sample_noframe(), ::testing::ExitedWithCode(0), "");
}

void
single_oneframe_sample()
{
    configure(256);

    // Collect and flush one sample with one frame
    auto h = ddup_start_sample();
    ddup_push_walltime(h, 1.0, 1);
    ddup_push_frame(h, "my_test_frame", "my_test_file", 1, 1);
    ddup_flush_sample(h);
    ddup_drop_sample(h);
    h = nullptr;

    std::unique_ptr<Datadog::UploaderConfig> config = std::make_unique<Datadog::UploaderConfig>();
    // Upload.  It'll fail, but whatever
    ddup_upload(std::move(config));

    std::exit(0);
}

TEST(UploadDeathTest, SingleSampleOneFrame)
{
    EXPECT_EXIT(single_oneframe_sample(), ::testing::ExitedWithCode(0), "");
}

void
single_manyframes_sample()
{
    configure(512);

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

    std::unique_ptr<Datadog::UploaderConfig> config = std::make_unique<Datadog::UploaderConfig>();
    // Upload.  It'll fail, but whatever
    ddup_upload(std::move(config));

    std::exit(0);
}

TEST(UploadDeathTest, SingleSampleManyFrames)
{
    EXPECT_EXIT(single_manyframes_sample(), ::testing::ExitedWithCode(0), "");
}

void
single_toomanyframes_sample()
{
    configure(512);

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

    std::unique_ptr<Datadog::UploaderConfig> config = std::make_unique<Datadog::UploaderConfig>();
    // Upload.  It'll fail, but whatever
    ddup_upload(std::move(config));

    std::exit(0);
}

TEST(UploadDeathTest, SingleSampleTooManyFrames)
{
    EXPECT_EXIT(single_toomanyframes_sample(), ::testing::ExitedWithCode(0), "");
}

void
lotsa_frames_lotsa_samples()
{
    configure(512);

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

    std::unique_ptr<Datadog::UploaderConfig> config = std::make_unique<Datadog::UploaderConfig>();
    // Upload.  It'll fail, but whatever
    ddup_upload(std::move(config));

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
