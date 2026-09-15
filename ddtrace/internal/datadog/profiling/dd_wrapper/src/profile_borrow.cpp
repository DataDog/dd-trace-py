#include "profile_borrow.hpp"
#include "profile.hpp"

Datadog::ProfileBorrow::ProfileBorrow(Profile& profile)
  : profile_ptr(&profile)
{
    profile_ptr->profile_mtx.lock();
}

Datadog::ProfileBorrow::~ProfileBorrow()
{
    if (profile_ptr) {
        profile_ptr->profile_mtx.unlock();
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
            profile_ptr->profile_mtx.unlock();
        }

        // Take ownership from other
        profile_ptr = other.profile_ptr;
        other.profile_ptr = nullptr;
    }
    return *this;
}

Datadog::ddprof::Profile&
Datadog::ProfileBorrow::profile()
{
    return *profile_ptr->cur_profile.value();
}

Datadog::ProfilerStats&
Datadog::ProfileBorrow::stats()
{
    return profile_ptr->cur_profiler_stats;
}
