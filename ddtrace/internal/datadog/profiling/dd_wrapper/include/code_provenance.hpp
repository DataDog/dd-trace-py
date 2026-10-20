#pragma once

#include <mutex>
#include <string>
#include <string_view>

namespace Datadog {

class CodeProvenance
{
  public:
    // Public static method to access the CodeProvenance instance
    static CodeProvenance& get_instance()
    {
        // An earlier atexit handler can still upload after static destructors run.
        // Keep the instance alive until the process exits.
        static CodeProvenance* instance = new CodeProvenance();
        return *instance;
    }

    // Delete copy constructor and assignment operator to prevent copies
    CodeProvenance(CodeProvenance const&) = delete;
    CodeProvenance& operator=(CodeProvenance const&) = delete;

    void set_json_str(std::string_view json_str);
    std::string get_json_snapshot();
    void prefork();
    void postfork_parent();
    void postfork_child();

  private:
    std::mutex json_mutex;
    std::string json_str;

    // Private Constructor/Destructor to prevent instantiation/deletion from outside
    CodeProvenance();
    ~CodeProvenance() = default;
};
}
