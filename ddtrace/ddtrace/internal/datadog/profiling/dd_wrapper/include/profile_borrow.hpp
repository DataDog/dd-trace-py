#pragma once

#include "profile.hpp"

namespace Datadog {

// Forward declaration
class Profile;

// RAII wrapper for borrowing both profile and stats under a single lock
class ProfileBorrow
{
  private:
    Profile* profile_ptr;

  public:
    explicit ProfileBorrow(Profile& profile);
    ~ProfileBorrow();

    // Disable copy
    ProfileBorrow(const ProfileBorrow&) = delete;
    ProfileBorrow& operator=(const ProfileBorrow&) = delete;

    // Enable move
    ProfileBorrow(ProfileBorrow&& other) noexcept;
    ProfileBorrow& operator=(ProfileBorrow&& other) noexcept;

    // Accessors
    ddog_prof_Profile& profile();
    ProfilerStats& stats();
};

} // namespace Datadog
