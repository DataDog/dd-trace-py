// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#pragma once

#include "profile.hpp"
#include "types.hpp"

#include <optional>

namespace Datadog {

class ProfileBuilder
{
    ProfileType type_mask = ProfileType::All;
    unsigned int max_nframes = 64;

  public:
    ProfileBuilder& add_type(ProfileType type);
    ProfileBuilder& add_type(unsigned int type);
    ProfileBuilder& set_max_nframes(unsigned int _max_nframes);

    std::optional<Profile> build();
};

} // namespace Datadog
