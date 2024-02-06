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
