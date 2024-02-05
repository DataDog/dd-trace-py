// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <optional>

namespace Datadog {

class SampleBuilder
{
    SampleType type_mask = SampleType::All;
    unsigned int max_nframes = 64;

  public:
    SampleBuilder& add_type(SampleType type);
    SampleBuilder& add_type(unsigned int type);
    SampleBuilder& set_max_nframes(unsigned int _max_nframes);

    std::optional<Sample> build();
};

} // namespace Datadog
