#include "ddup_interface.hpp"
#include "profiler_state.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

// Tests for ddup_get_profiler_runtime_stats() and the cumulative counters
// on ProfilerState that back the runtime.python.profiler.* DogStatsD metrics.

void
runtime_stats_before_init()
{
    // Before initialization, ddup_get_profiler_runtime_stats should return false
    size_t sc = 999, sec = 999, cmec = 999, scctu = 999;
    int64_t si = 999, atc = 999, gc = 999, htc = 999, stc = 999, stec = 999, fcme = 999;

    bool ok = ddup_get_profiler_runtime_stats(&sc, &sec, &cmec, &scctu, &si, &atc, &gc, &htc, &stc, &stec, &fcme);
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

    // After init, cumulative counters should be zero and the call should succeed
    size_t sc, sec, cmec, scctu;
    int64_t si, atc, gc, htc, stc, stec, fcme;

    bool ok = ddup_get_profiler_runtime_stats(&sc, &sec, &cmec, &scctu, &si, &atc, &gc, &htc, &stc, &stec, &fcme);
    assert(ok);
    assert(sc == 0);
    assert(sec == 0);
    assert(cmec == 0);
    assert(scctu == 0);

    // Gauges that have not been set should be -1 (sentinel for std::optional nullopt)
    assert(si == -1);
    assert(atc == -1);

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
    size_t sc, sec, cmec, scctu;
    int64_t si, atc, gc, htc, stc, stec, fcme;

    bool ok = ddup_get_profiler_runtime_stats(&sc, &sec, &cmec, &scctu, &si, &atc, &gc, &htc, &stc, &stec, &fcme);
    assert(ok);
    assert(sc == 10);
    assert(sec == 3);
    assert(cmec == 1);
    assert(scctu == 500);

    // Bump again and verify they accumulate
    state.cumulative_sample_count.fetch_add(5, std::memory_order_relaxed);
    ok = ddup_get_profiler_runtime_stats(&sc, &sec, &cmec, &scctu, &si, &atc, &gc, &htc, &stc, &stec, &fcme);
    assert(ok);
    assert(sc == 15);

    std::exit(0);
}

TEST(RuntimeStatsDeathTest, CumulativeCountersSurviveUpload)
{
    EXPECT_EXIT(cumulative_counters_survive_upload(), ::testing::ExitedWithCode(0), "");
}
