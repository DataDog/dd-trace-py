#include "ddup_interface.hpp"
#include "profiler_stats.hpp"
#include "sample.hpp"
#include "sample_manager.hpp"
#include "test_utils.hpp"

#include <gtest/gtest.h>

#include <iostream>
#include <string>

namespace {

constexpr size_t kMaxFrames = 64;
constexpr size_t kDeepStack = 100;

const Datadog::FrameTruncationStats&
collector_stats(const Datadog::FrameStats& stats, Datadog::FrameCollector collector)
{
    return stats.collectors[static_cast<size_t>(collector)];
}

void
push_frames(Datadog::Sample* sample, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        const std::string name = "func_" + std::to_string(i);
        sample->push_frame(name, "file.py", 0, static_cast<int64_t>(i));
    }
}

// Each check prints what went wrong so a failure in the death-test child is readable.
bool
check(bool ok, const char* what)
{
    if (!ok) {
        std::cerr << "check failed: " << what << std::endl;
    }
    return ok;
}

void
records_truncation_per_collector()
{
    configure("my_test_service",
              "my_test_env",
              "0.0.1",
              "https://127.0.0.1:9126",
              "cpython",
              "3.10.6",
              "3.100",
              static_cast<int>(kMaxFrames));

    // Stack sample: push_frame counts each frame past the limit.
    auto* h = Datadog::SampleManager::start_sample();
    h->push_cputime(1, 1);
    h->push_walltime(1, 1);
    push_frames(h, kDeepStack);
    h->flush_sample();
    Datadog::SampleManager::drop_sample(h);

    // Lock sample that fits under the limit.
    h = Datadog::SampleManager::start_sample();
    h->push_acquire(1, 1);
    push_frames(h, 10);
    h->flush_sample();
    Datadog::SampleManager::drop_sample(h);

    // Memalloc sample: kept frames plus an explicit omitted count, exported
    // once as an allocation and once more as a live heap sample.
    Datadog::Sample mem(static_cast<Datadog::SampleType>(Datadog::SampleType::Allocation | Datadog::SampleType::Heap),
                        static_cast<unsigned int>(kMaxFrames));
    mem.push_alloc(100, 1);
    push_frames(&mem, kMaxFrames);
    mem.incr_dropped_frames(kDeepStack - kMaxFrames);
    mem.set_walk_ns(1234);
    mem.export_sample();
    mem.reset_alloc();
    mem.push_heap(100, 1);
    mem.export_sample();

    bool ok = true;
    Datadog::FrameStats before_upload;
    {
        auto borrowed = Datadog::Sample::profile_borrow();
        before_upload = borrowed.stats().get_frame_stats();
        const std::string json = borrowed.stats().get_internal_metadata_json();
        ok &= check(json.find(R"("frame_truncation": {"stack": {)") != std::string::npos, "json has stack entry");
        ok &= check(json.find(R"("memalloc_walk_ns": 1234)") != std::string::npos, "json has memalloc_walk_ns");
    }

    const auto& stack = collector_stats(before_upload, Datadog::FrameCollector::stack);
    ok &= check(stack.samples == 1, "stack samples");
    ok &= check(stack.samples_truncated == 1, "stack samples_truncated");
    ok &= check(stack.frames_dropped == kDeepStack - kMaxFrames, "stack frames_dropped");
    ok &= check(stack.max_depth == kDeepStack, "stack max_depth");
    ok &= check(stack.max_frames == kMaxFrames, "stack max_frames");
    // 100 frames falls in the 65..128 bucket
    ok &= check(stack.depth_buckets[0] == 0 && stack.depth_buckets[1] == 1, "stack depth bucket");

    const auto& lock = collector_stats(before_upload, Datadog::FrameCollector::lock);
    ok &= check(lock.samples == 1 && lock.samples_truncated == 0, "lock counts");
    ok &= check(lock.max_depth == 10 && lock.depth_buckets[0] == 1, "lock depth");

    for (auto collector : { Datadog::FrameCollector::alloc, Datadog::FrameCollector::heap }) {
        const auto& mem_stats = collector_stats(before_upload, collector);
        ok &= check(mem_stats.samples == 1 && mem_stats.samples_truncated == 1, "memalloc counts");
        ok &= check(mem_stats.frames_dropped == kDeepStack - kMaxFrames, "memalloc frames_dropped");
        ok &= check(mem_stats.max_depth == kDeepStack, "memalloc max_depth");
        ok &= check(mem_stats.depth_buckets[1] == 1, "memalloc depth bucket");
    }
    // Walk time is reported once, with the allocation export, not again for the heap export.
    ok &= check(before_upload.memalloc_walk_ns == 1234, "memalloc_walk_ns");

    // Upload fails (no agent), but the stats are still handed off and reset.
    ddup_upload();

    Datadog::FrameStats taken;
    ddup_take_frame_stats(&taken);
    ok &= check(collector_stats(taken, Datadog::FrameCollector::stack).max_depth == kDeepStack, "taken stack");
    ok &= check(taken.memalloc_walk_ns == 1234, "taken memalloc_walk_ns");

    Datadog::FrameStats taken_again;
    ddup_take_frame_stats(&taken_again);
    ok &= check(collector_stats(taken_again, Datadog::FrameCollector::stack).samples == 0, "second take is empty");

    {
        auto borrowed = Datadog::Sample::profile_borrow();
        const auto& after = borrowed.stats().get_frame_stats();
        ok &= check(collector_stats(after, Datadog::FrameCollector::stack).samples == 0, "stats reset after upload");
    }

    std::exit(ok ? 0 : 1);
}

} // namespace

TEST(FrameTruncationStats, DepthBuckets)
{
    Datadog::FrameTruncationStats stats;
    stats.record(64, 0, 64);
    stats.record(64, 1, 64);
    stats.record(64, 192, 64);
    stats.record(64, 535, 64);
    stats.record(64, 536, 64);

    EXPECT_EQ(stats.samples, 5u);
    EXPECT_EQ(stats.samples_truncated, 4u);
    EXPECT_EQ(stats.max_depth, 600u);
    EXPECT_EQ(stats.depth_buckets[0], 1u); // 64
    EXPECT_EQ(stats.depth_buckets[1], 1u); // 65
    EXPECT_EQ(stats.depth_buckets[2], 1u); // 256
    EXPECT_EQ(stats.depth_buckets[3], 1u); // 599
    EXPECT_EQ(stats.depth_buckets[4], 1u); // 600
}

TEST(FrameTruncationDeathTest, RecordsTruncationPerCollector)
{
    EXPECT_EXIT(records_truncation_per_collector(), ::testing::ExitedWithCode(0), "");
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    (void)(::testing::GTEST_FLAG(death_test_style) = "threadsafe");
    return RUN_ALL_TESTS();
}
