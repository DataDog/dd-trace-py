#include "profile_borrow.hpp"
#include "profile.hpp"

Datadog::ProfileBorrow::ProfileBorrow(Profile& profile)
  : profile_ptr(&profile)
{
    // Lock the mutex on construction
    profile_ptr->profile_borrow_internal();
}

Datadog::ProfileBorrow::~ProfileBorrow()
{
    if (profile_ptr) {
        profile_ptr->profile_release();
    }
}

Datadog::ProfileBorrow::ProfileBorrow(ProfileBorrow&& other) noexcept
  : profile_ptr(other.profile_ptr)
{
    other.profile_ptr = nullptr;
}

Datadog::ProfileBorrow&
Datadog::ProfileBorrow::operator=(ProfileBorrow&& other) noexcept
{
    if (this != &other) {
        // Release current lock if any
        if (profile_ptr) {
            profile_ptr->profile_release();
        }

        // Take ownership from other
        profile_ptr = other.profile_ptr;
        other.profile_ptr = nullptr;
    }
    return *this;
}

rust::Box<Datadog::ddprof::EncodedProfile>
Datadog::ProfileBorrow::serialize()
{
    return profile_ptr->cur_profile.value()->serialize();
}

std::vector<std::uint8_t>
Datadog::ProfileBorrow::serialize_to_vec()
{
    auto encoded = profile_ptr->cur_profile.value()->serialize_to_vec();
    return std::vector<std::uint8_t>(encoded.data(), encoded.data() + encoded.size());
}

bool
Datadog::ProfileBorrow::add_endpoint(std::int64_t local_root_span_id, std::string_view endpoint)
{
    return profile_ptr->add_endpoint(local_root_span_id, endpoint);
}

bool
Datadog::ProfileBorrow::add_endpoint_count(std::string_view endpoint, std::int64_t value)
{
    return profile_ptr->add_endpoint_count(endpoint, value);
}

Datadog::ProfilerStats&
Datadog::ProfileBorrow::stats()
{
    return profile_ptr->cur_profiler_stats;
}
