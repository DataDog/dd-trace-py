// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#pragma once

#include "profile.hpp"
#include "profile_builder.hpp"
#include "uploader_builder.hpp"

#include <map>
#include <mutex>
#include <thread>

namespace Datadog {

// Global state for profiling.  Manages thread-keys caches of profiles
class ProfileGlobalStorage
{
  private:
    std::mutex profile_storage_mtx;
    std::map<std::thread::id, Profile> profile_cache;

    // TODO delete some constructors?
    //  ProfileGlobalStorage();
    //  ProfileGlobalStorage(const ProfileGlobalStorage &) = delete;
    //  ProfileGlobalStorage &operator=(const ProfileGlobalStorage &) = delete;
    //  ProfileGlobalStorage(ProfileGlobalStorage &&) = delete;

  public:
    inline static UploaderBuilder uploader_builder{};
    inline static ProfileBuilder profile_builder{};

    static ProfileGlobalStorage& get_singleton();
    static Profile& get(std::thread::id id);
    static void clear();
};

} // namespace Datadog
