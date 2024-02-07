#include "sample_builder.hpp"
#include "types.hpp"

using namespace Datadog;

void
SampleBuilder::add_type(SampleType type)
{
    type_mask = static_cast<SampleType>((type_mask | type) & SampleType::All);
}

void
SampleBuilder::add_type(unsigned int type)
{
    add_type(static_cast<SampleType>(type));
}

void
SampleBuilder::set_max_nframes(unsigned int _max_nframes)
{
    const unsigned int backend_max_nframes = 512;

    if (_max_nframes > 0)
        max_nframes = _max_nframes;

    // If the user has requested more than we're allowed to give, reduce the limit and warn the user.
    if (max_nframes > backend_max_nframes) {
        std::cerr << "Requested limit of " << max_nframes << " will be reduced to " << backend_max_nframes << std::endl;
        max_nframes = backend_max_nframes;
    }
}

Sample
SampleBuilder::build()
{
    // Constructor may throw an exception.  We propagate it upward.
    return Sample(type_mask, max_nframes);
}
