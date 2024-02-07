#include "sample_manager.hpp"
#include "types.hpp"

using namespace Datadog;

void
SampleManager::add_type(SampleType type)
{
    type_mask = static_cast<SampleType>((type_mask | type) & SampleType::All);
}

void
SampleManager::add_type(unsigned int type)
{
    add_type(static_cast<SampleType>(type));
}

void
SampleManager::set_max_nframes(unsigned int _max_nframes)
{
    const unsigned int backend_max_nframes = 512;

    if (_max_nframes > 0)
        max_nframes = _max_nframes;

    // If the user has requested more than we're allowed to give, reduce the limit and warn the user.
    if (max_nframes > backend_max_nframes) {
        std::cerr << "Requested limit of " << max_nframes << " will be reduced to " << backend_max_nframes << std::endl;
        max_nframes = backend_max_nframes;
    }
}

bool
SampleManager::take_handler(SampleHandle handle)
{
    // Bounds check
    if (handle == SampleHandle::Invalid || static_cast<size_t>(handle) >= handler_state.size()) {
        return false;
    }

    // If there's no active handler in that category, take it
    if (handler_state[static_cast<size_t>(handle)].exchange(true)) {
        return false;
    }
    return true;
}

void
SampleManager::release_handler(SampleHandle handle)
{
    // Bounds check
    if (handle == SampleHandle::Invalid || static_cast<size_t>(handle) >= handler_state.size()) {
        return;
    }
    handler_state[static_cast<size_t>(handle)].exchange(false);
}

void
SampleManager::build_storage()
{
    // Initialize the static storage for Profile state
    Sample::profile_state.one_time_init(type_mask, max_nframes);

    // Since the construct for Sample might throw, note we have to catch it eventually
    // Strongly assume that by the time we get here, the user is done trying to configure us.
    storage->clear();
    for (size_t i = 0; i < handler_state.size(); ++i) {
        storage->emplace_back(type_mask, max_nframes);
    }
}

SampleHandle
SampleManager::start_sample(SampleHandle requested)
{
    static unsigned int failure_count = 0;
    constexpr unsigned int max_failures = 3;
    auto ret = SampleHandle::Invalid;

    // take_handler will do the bounds check on `requested`
    if (take_handler(requested)) {
        ret = requested;

        // If the storage hasn't been initialized yet, do so now
        // If we've failed to initialize it too many times, give up forever
        if (!storage.has_value() && failure_count < max_failures) {
            try {
                build_storage();
            } catch (const std::exception& e) {
                std::cerr << "Failed to initialize storage: " << e.what() << std::endl;
                ++failure_count;
                return SampleHandle::Invalid;
            }
        }

        // If we're here, the handle was taken and the storage was initialized, call whatever setup the sample needs
        storage->at(static_cast<size_t>(ret)).start_sample();
    }
    return ret;
}

SampleHandle
SampleManager::start_sample(unsigned int requested)
{
    return start_sample(static_cast<SampleHandle>(requested));
}

void
SampleManager::reset_profile()
{
    Sample::profile_state.reset();
}
