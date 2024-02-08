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

    if (_max_nframes > 0) {
        max_nframes = _max_nframes;
    }

    // If the user has requested more than we're allowed to give, reduce the limit and warn the user.
    if (max_nframes > backend_max_nframes) {
        std::cerr << "Requested limit of " << max_nframes << " will be reduced to " << backend_max_nframes << std::endl;
        max_nframes = backend_max_nframes;
    }
}

bool
SampleManager::take_handle(SampleHandle handle)
{
    // Bounds check
    const unsigned int idx = static_cast<unsigned int>(handle);
    if (handle == SampleHandle::Invalid || idx >= static_cast<size_t>(SampleHandle::Length_)) {
        return false;
    }

    // If there's no active handle in that category, take it
    bool expected = false;
    if (handle_state[idx].compare_exchange_strong(expected, true)) {
        return true;
    }
    return false;
}

void
SampleManager::release_handle(SampleHandle handle)
{
    // Bounds check
    const unsigned int idx = static_cast<unsigned int>(handle);
    if (handle == SampleHandle::Invalid || idx >= static_cast<size_t>(SampleHandle::Length_)) {
        return;
    }
    handle_state[static_cast<size_t>(handle)].store(false);
}

void
SampleManager::build_storage()
{
    // Initialize the static storage for Profile state
    Sample::profile_state.one_time_init(type_mask, max_nframes);

    // Since the construct for Sample might throw, note we have to catch it eventually
    // Strongly assume that by the time we get here, the user is done trying to configure us.
    storage.clear();
    for (size_t i = 0; i < handle_state.size(); ++i) {
        storage.emplace_back(type_mask, max_nframes);
    }
}

SampleHandle
SampleManager::start_sample(SampleHandle requested)
{
    constexpr unsigned int max_failures = 3;
    auto ret = SampleHandle::Invalid;

    // take_handle will do the bounds check on `requested`
    if (take_handle(requested)) {
        ret = requested;

        // If the storage hasn't been initialized yet, do so now
        // If we've failed to initialize it too many times, give up forever
        static unsigned int failure_count = 0;
        if (storage.size() < handle_state.size() && failure_count < max_failures) {
            try {
                build_storage();
            } catch (const std::exception& e) {
                std::cerr << "Failed to initialize storage: " << e.what() << std::endl;
                ++failure_count;
                return SampleHandle::Invalid;
            }
        }

        // If somehow we _still_ don't have storage, give up forever
        if (storage.size() < handle_state.size()) {
            return SampleHandle::Invalid;
        }

        // If we're here, the handle was taken and the storage was initialized, call whatever setup the sample needs
        storage[static_cast<size_t>(ret)].start_sample();
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
