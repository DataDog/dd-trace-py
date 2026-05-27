#include "ddup_interface.hpp"
#include "profiler_state.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

// Tests for ddup_get_profiler_runtime_stats() and the cumulative counters
// on ProfilerState that back the runtime.python.profiler.* DogStatsD metrics.

void
runtime_stats_before_init()
{
    ProfilerRuntimeStats stats{};
    bool ok = ddup_get_profiler_runtime_stats(&stats);
    assert(!ok);

    std::exit(0);
}

TEST(RuntimeStatsDeathTest, BeforeInit)
{
    EXPECT_EXIT(runtime_stats_before_init(), ::testing::ExitedWithCode(0), "");
}

void
runtime_stats_after_init()
{
    configure("my_service", "my_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);

    ProfilerRuntimeStats stats{};
    bool ok = ddup_get_profiler_runtime_stats(&stats);
    assert(ok);
    assert(stats.sample_count == 0);
    assert(stats.sampling_event_count == 0);
    assert(stats.copy_memory_error_count == 0);
    assert(stats.sample_capture_cpu_time_us == 0);

    // Gauges that have not been set should be -1 (sentinel for std::optional nullopt)
    assert(stats.sampling_interval_us == -1);
    assert(stats.asyncio_task_count == -1);

    std::exit(0);
}

TEST(RuntimeStatsDeathTest, AfterInit)
{
    EXPECT_EXIT(runtime_stats_after_init(), ::testing::ExitedWithCode(0), "");
}

void
cumulative_counters_survive_upload()
{
    configure("my_service", "my_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);

    auto& state = Datadog::ProfilerState::get();

    // Manually bump cumulative counters (simulating what sampler.cpp does)
    state.cumulative_sample_count.fetch_add(10, std::memory_order_relaxed);
    state.cumulative_sampling_event_count.fetch_add(3, std::memory_order_relaxed);
    state.cumulative_copy_memory_error_count.fetch_add(1, std::memory_order_relaxed);
    state.cumulative_sample_capture_cpu_time_us.fetch_add(500, std::memory_order_relaxed);

    // Perform an upload (which triggers ProfilerStats swap/reset)
    ddup_upload();

    // Verify cumulative counters are NOT reset by upload
    ProfilerRuntimeStats stats{};
    bool ok = ddup_get_profiler_runtime_stats(&stats);
    assert(ok);
    assert(stats.sample_count == 10);
    assert(stats.sampling_event_count == 3);
    assert(stats.copy_memory_error_count == 1);
    assert(stats.sample_capture_cpu_time_us == 500);

    // Bump again and verify they accumulate
    state.cumulative_sample_count.fetch_add(5, std::memory_order_relaxed);
    ok = ddup_get_profiler_runtime_stats(&stats);
    assert(ok);
    assert(stats.sample_count == 15);

    std::exit(0);
}

TEST(RuntimeStatsDeathTest, CumulativeCountersSurviveUpload)
{
    EXPECT_EXIT(cumulative_counters_survive_upload(), ::testing::ExitedWithCode(0), "");
}
