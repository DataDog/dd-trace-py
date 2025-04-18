#include "sample_manager.hpp"
#include "types.hpp"

void
Datadog::SampleManager::add_type(unsigned int type)
{
    type_mask = static_cast<SampleType>((static_cast<unsigned int>(type_mask) | type)) & SampleType::All;
}

void
Datadog::SampleManager::set_max_nframes(unsigned int _max_nframes)
{
    if (_max_nframes > 0) {
        max_nframes = _max_nframes;
    }

    // If the user has requested more than we're allowed to give, reduce the limit and warn the user.
    if (max_nframes > g_backend_max_nframes) {
        // We don't emit an error here for now.
        max_nframes = g_backend_max_nframes;
    }
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
    if (sample_pool == nullptr) {
        return new Datadog::Sample(type_mask, max_nframes); // NOLINT(cppcoreguidelines-owning-memory)
    }

    auto sample_opt = sample_pool->take_sample();
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
    if (sample_pool == nullptr) {
        delete sample; // NOLINT(cppcoreguidelines-owning-memory)
        return;
    }

    sample->clear_buffers();

    std::optional<Sample*> result_opt = sample_pool->return_sample(sample);
    // If the pool is full, the pool returns the pointer and we need to delete the sample.
    if (result_opt.has_value()) {
        delete result_opt.value(); // NOLINT(cppcoreguidelines-owning-memory)
    }
}

void
Datadog::SampleManager::postfork_child()
{
    Datadog::Sample::postfork_child();
    if (sample_pool != nullptr) {
        // Clear the pool to make sure it's in a consistent state.
        // Suppose there was a thread that was adding/removing sample from the pool
        // and the fork happened in the middle of that operation.
        sample_pool = std::make_unique<SynchronizedSamplePool>(sample_pool_capacity);
    }

    Datadog::Sample::profile_state.one_time_init_impl(type_mask, max_nframes);
    Datadog::Sample::profile_state.cycle_buffers();
}

void
Datadog::SampleManager::init()
{
    if (sample_pool == nullptr) {
        sample_pool = std::make_unique<SynchronizedSamplePool>(sample_pool_capacity);
    }
    Datadog::Sample::profile_state.one_time_init(type_mask, max_nframes);
}
