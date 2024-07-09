#include "sample_manager.hpp"
#include "types.hpp"

void
Datadog::SampleManager::add_type(unsigned int type)
{
    type_mask = static_cast<SampleType>((type_mask | type) & SampleType::All);
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

Datadog::Sample*
Datadog::SampleManager::start_sample()
{
    return new Datadog::Sample(type_mask, max_nframes); // NOLINT(cppcoreguidelines-owning-memory)
}

void
Datadog::SampleManager::drop_sample(Datadog::Sample* sample)
{
    delete sample; // NOLINT(cppcoreguidelines-owning-memory)
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
