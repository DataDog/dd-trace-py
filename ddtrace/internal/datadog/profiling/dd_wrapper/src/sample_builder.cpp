#include "sample_builder.hpp"
#include "types.hpp"

using namespace Datadog;

SampleBuilder&
SampleBuilder::add_type(SampleType type)
{
    unsigned int mask_as_int = (type_mask | type) & SampleType::All;
    type_mask = static_cast<SampleType>(mask_as_int);
    return *this;
}

SampleBuilder&
SampleBuilder::add_type(unsigned int type)
{
    return add_type(static_cast<SampleType>(type));
}

SampleBuilder&
SampleBuilder::set_max_nframes(unsigned int _max_nframes)
{
    const unsigned int backend_max_nframes = 512;

    if (_max_nframes > 0)
        max_nframes = _max_nframes;
    if (max_nframes > backend_max_nframes)
        max_nframes = backend_max_nframes;
    return *this;
}

std::optional<Sample>
SampleBuilder::build()
{
    try {
        return Sample(type_mask, max_nframes);
    } catch (const std::exception& e) {
        return std::nullopt;
    }
}
