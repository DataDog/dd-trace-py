#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <optional>

namespace Datadog {

class SampleBuilder
{
    static inline SampleType type_mask{ SampleType::All };
    static inline unsigned int max_nframes{ 64 };

  public:
    static void add_type(SampleType type);
    static void add_type(unsigned int type);
    static void set_max_nframes(unsigned int _max_nframes);

    static Sample build();
};

} // namespace Datadog
