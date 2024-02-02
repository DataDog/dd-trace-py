#include "profile_builder.hpp"
#include "types.hpp"

using namespace Datadog;

ProfileBuilder&
ProfileBuilder::add_type(ProfileType type)
{
    unsigned int mask_as_int = (type_mask | type) & ProfileType::All;
    type_mask = static_cast<ProfileType>(mask_as_int);
    return *this;
}

ProfileBuilder&
ProfileBuilder::add_type(unsigned int type)
{
    return add_type(static_cast<ProfileType>(type));
}

ProfileBuilder&
ProfileBuilder::set_max_nframes(unsigned int _max_nframes)
{
    if (_max_nframes > 0)
        max_nframes = _max_nframes;
    return *this;
}

std::optional<Profile>
ProfileBuilder::build()
{
    try {
        return Profile(type_mask, max_nframes);
    } catch (const std::exception& e) {
        return std::nullopt;
    }
}
