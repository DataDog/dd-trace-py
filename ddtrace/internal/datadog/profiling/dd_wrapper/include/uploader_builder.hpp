#pragma once

#include "uploader.hpp"
#include "uploader_config.hpp"

#include <string>
#include <variant>

namespace Datadog {
class UploaderBuilder
{
  public:
    static std::variant<Uploader, std::string> build(std::unique_ptr<UploaderConfig> config);
};

} // namespace Datadog
