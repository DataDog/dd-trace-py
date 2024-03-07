#include "sample_manager.hpp"
#include "types.hpp"

using namespace Datadog;

void
SampleManager::add_type(unsigned int type)
{
    type_mask = static_cast<SampleType>((type_mask | type) & SampleType::All);
}

void
SampleManager::set_max_nframes(unsigned int _max_nframes)
{
    if (_max_nframes > 0) {
        max_nframes = _max_nframes;
    }

    // If the user has requested more than we're allowed to give, reduce the limit and warn the user.
    if (max_nframes > g_backend_max_nframes) {
        std::cerr << "Requested limit of " << max_nframes << " will be reduced to " << g_backend_max_nframes
                  << std::endl;
        max_nframes = g_backend_max_nframes;
    }
}

Sample*
SampleManager::start_sample()
{
    return new Sample(type_mask, max_nframes);
}

void
SampleManager::postfork_child()
{
    Sample::postfork_child();
}

void
SampleManager::init()
{
    Sample::profile_state.one_time_init(type_mask, max_nframes);
}
