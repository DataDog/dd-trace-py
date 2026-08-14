#include "stack_renderer.hpp"

#include "dd_wrapper/include/ddup_interface.hpp"
#include "dd_wrapper/include/sample_manager.hpp"
#include "dd_wrapper/include/static_sample_pool.hpp"

#include <gtest/gtest.h>

#include <cstddef>
#include <cstdint>
#include <vector>

using namespace Datadog;

namespace {

void
fill_sample_pool()
{
    std::vector<Sample*> samples;
    samples.reserve(StaticSamplePool::CAPACITY);
    for (size_t i = 0; i < StaticSamplePool::CAPACITY; ++i) {
        samples.push_back(SampleManager::start_sample());
    }
    for (auto* sample : samples) {
        SampleManager::drop_sample(sample);
    }
}

size_t
count_available_samples()
{
    std::vector<Sample*> samples;
    while (auto sample = StaticSamplePool::take_sample()) {
        samples.push_back(*sample);
    }

    for (auto* sample : samples) {
        auto leftover = StaticSamplePool::return_sample(sample);
        EXPECT_FALSE(leftover.has_value());
        if (leftover.has_value()) {
            delete *leftover; // NOLINT(cppcoreguidelines-owning-memory)
        }
    }
    return samples.size();
}

class StackRendererSampleLifecycle : public ::testing::Test
{
  protected:
    static void SetUpTestSuite()
    {
        ddup_config_service("test_service");
        ddup_config_env("test_env");
        ddup_config_version("0.0.1");
        ddup_config_url("http://localhost:8126");
        ddup_config_max_nframes(64);
        ddup_start();
    }

    void SetUp() override
    {
        fill_sample_pool();
        ASSERT_EQ(count_available_samples(), StaticSamplePool::CAPACITY);
    }
};

} // namespace

TEST_F(StackRendererSampleLifecycle, StartingNextCycleRecoversAbandonedSample)
{
    StackRenderer renderer;

    // Simulate ThreadInfo::sample() returning after render_thread_begin() without reaching render_stack_end().
    // ThreadInfo::update_cpu_time() normalizes deterministically constructible invalid-clock errors to success, so
    // exercise the renderer lifecycle directly rather than relying on platform-specific syscall interposition.
    for (size_t i = 0; i < StaticSamplePool::CAPACITY * 4; ++i) {
        renderer.render_thread_begin(nullptr, "test-thread", 0, i + 1, i + 1);
    }
    renderer.abort_sample();

    EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY)
      << "starting a new render cycle leaked the Sample abandoned by the previous cycle";
}

TEST_F(StackRendererSampleLifecycle, DestructionRecoversAbandonedSample)
{
    {
        StackRenderer renderer;
        renderer.render_thread_begin(nullptr, "test-thread", 0, 1, 1);
    }

    EXPECT_EQ(count_available_samples(), StaticSamplePool::CAPACITY)
      << "renderer destruction leaked an in-flight Sample";
}
