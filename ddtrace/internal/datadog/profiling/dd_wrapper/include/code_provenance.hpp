#pragma once

#include <string>
#include <string_view>

namespace Datadog {

class CodeProvenance
{
  public:
    // Public static method to access the CodeProvenance instance
    static CodeProvenance& get_instance()
    {
        static CodeProvenance instance;
        return instance;
    }

    // Delete copy constructor and assignment operator to prevent copies
    CodeProvenance(CodeProvenance const&) = delete;
    CodeProvenance& operator=(CodeProvenance const&) = delete;

    void set_json_str(std::string_view json_str);
    std::string_view get_json_str();

  private:
    std::string json_str;

    // Private Constructor/Destructor to prevent instantiation/deletion from outside
    CodeProvenance() = default;
    ~CodeProvenance() = default;
};
}
