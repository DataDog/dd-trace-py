#pragma once

#include <string>
#include <variant>

namespace Datadog {

struct ErrorMessage
{
    std::string message;
};

template<typename T>
using Result = std::variant<T, ErrorMessage>;

} // namespace Datadog
