#pragma once

#include <string>
#include <string_view>

namespace Datadog {

class CodeProvenance
{
  public:
    static CodeProvenance& get_instance();

    // Delete copy constructor and assignment operator to prevent copies
    CodeProvenance(CodeProvenance const&) = delete;
    CodeProvenance& operator=(CodeProvenance const&) = delete;

    void set_file_path(std::string_view file_path);
    std::string_view get_json_str();

  private:
    std::string json_str;

    // Private Constructor/Destructor to prevent instantiation/deletion from outside
    CodeProvenance() = default;
    ~CodeProvenance() = default;
};
}
