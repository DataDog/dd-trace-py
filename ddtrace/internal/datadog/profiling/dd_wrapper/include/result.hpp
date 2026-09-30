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

template<typename T>
const ErrorMessage*
error_if_any(const Result<T>& result)
{
    return std::get_if<ErrorMessage>(&result);
}

} // namespace Datadog
