#include "stack_renderer.hpp"

#include "dd_wrapper/include/ddup_interface.hpp"
#include "dd_wrapper/include/sample_manager.hpp"
#include "dd_wrapper/include/static_sample_pool.hpp"

#include <gtest/gtest.h>

#include <cstddef>
#include <vector>

using namespace Datadog;

namespace {

// Drain every sample the pool will currently hand out, count them, then return them all.
// Leaves the pool in its original state and reports how many samples were available.
size_t
count_available_pool_samples()
{
    std::vector<Sample*> taken;
    while (auto s = StaticSamplePool::take_sample()) {
        taken.push_back(*s);
    }
    const size_t available = taken.size();
    for (auto* s : taken) {
        if (auto leftover = StaticSamplePool::return_sample(s)) {
            // Pool full -- should not happen here since we just drained it.
            delete *leftover; // NOLINT(cppcoreguidelines-owning-memory)
        }
    }
    return available;
}

} // namespace

// Regression test for the sample-pool leak.
//
// render_thread_begin() checks a Sample out of the fixed-capacity StaticSamplePool. Prior to the fix it overwrote the
// in-flight pointer without returning it whenever a sampling cycle ended early -- e.g. one of echion's
// ThreadInfo::sample() error paths (CpuTimeError, ThreadInfoError) returned after render_thread_begin() but before
// render_stack_end(). Each such cycle permanently drained one pool slot; once the pool was drained every subsequent
// sample was heap-allocated via `new` and leaked. The fix returns any in-flight sample before checking out a new one.
TEST(StackRendererSamplePool, ThreadBeginWithoutEndDoesNotDrainPool)
{
    // Initialize profiler state so SampleManager::start_sample() can build valid samples.
    ddup_config_service("test_service");
    ddup_config_env("test_env");
    ddup_config_version("0.0.1");
    ddup_config_url("http://localhost:8126");
    ddup_config_max_nframes(256);
    ddup_start();

    ASSERT_EQ(count_available_pool_samples(), StaticSamplePool::CAPACITY);

    StackRenderer renderer;

    // Simulate many sampling cycles that begin a thread render but never reach render_stack_end() -- the production
    // trigger. Run well past CAPACITY so that a leak would fully drain the pool.
    for (size_t i = 0; i < StaticSamplePool::CAPACITY * 4 + 4; ++i) {
        renderer.render_thread_begin(nullptr, "test_thread", 0, /*thread_id=*/i + 1, /*native_id=*/i + 1);
    }
    // Close the final, legitimately in-flight sample.
    renderer.render_stack_end();

    // With the fix, every render_thread_begin() returns the prior orphan before taking a new sample, so the pool is
    // fully restored. Without the fix only the single sample recovered by the final render_stack_end() is available.
    EXPECT_EQ(count_available_pool_samples(), StaticSamplePool::CAPACITY)
      << "render_thread_begin() leaked sample-pool slots when a sampling cycle ended without render_stack_end()";
}
