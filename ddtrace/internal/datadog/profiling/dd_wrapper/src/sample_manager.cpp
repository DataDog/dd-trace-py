#include "sample_manager.hpp"
#include "static_sample_pool.hpp"
#include "types.hpp"

void
Datadog::SampleManager::add_type(unsigned int type)
{
    type_mask = static_cast<SampleType>((type_mask | type) & SampleType::All);
}

void
Datadog::SampleManager::set_timeline(bool enable)
{
    Datadog::Sample::set_timeline(enable);
}

void
Datadog::SampleManager::set_sample_pool_capacity(size_t capacity)
{
    if (capacity > 0) {
        sample_pool_capacity = capacity;
    }
}

Datadog::Sample*
Datadog::SampleManager::start_sample()
{
    auto sample_opt = StaticSamplePool::take_sample();
    if (sample_opt.has_value()) {
        return sample_opt.value();
    }

    // Create a new Sample if we failed to get one.
    // Note that this could be leaked if another thread calls fork() before
    // the Sample is returned to the pool.
    return new Datadog::Sample(type_mask, max_nframes); // NOLINT(cppcoreguidelines-owning-memory)
}

void
Datadog::SampleManager::drop_sample(Datadog::Sample* sample)
{
    std::optional<Sample*> result_opt = StaticSamplePool::return_sample(sample);
    // If the pool is full, the pool returns the pointer and we need to delete the sample.
    if (result_opt.has_value()) {
        delete result_opt.value(); // NOLINT(cppcoreguidelines-owning-memory)
    }
}

void
Datadog::SampleManager::postfork_child()
{
    Datadog::Sample::postfork_child();
}

void
Datadog::SampleManager::init()
{
    Datadog::Sample::profile_state.one_time_init(type_mask, max_nframes);
}
