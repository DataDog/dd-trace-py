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

ddog_prof_Profile&
Datadog::ProfileBorrow::profile()
{
    return profile_ptr->cur_profile;
}

Datadog::ProfilerStats&
Datadog::ProfileBorrow::stats()
{
    return profile_ptr->cur_profiler_stats;
}
