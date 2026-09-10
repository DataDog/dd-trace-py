#pragma once

#include "profile.hpp"

#include <cstdint>
#include <string_view>

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
    rust::Box<ddprof::EncodedProfile> serialize();
    bool add_endpoint(std::int64_t local_root_span_id, std::string_view endpoint);
    bool add_endpoint_count(std::string_view endpoint, std::int64_t value);
    ProfilerStats& stats();
};

} // namespace Datadog
