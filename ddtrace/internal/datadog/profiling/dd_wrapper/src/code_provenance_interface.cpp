#include "code_provenance_interface.hpp"

#include "code_provenance.hpp"

#include <string_view>
#include <unordered_map>

void
code_provenance_enable(bool enable)
{
    Datadog::CodeProvenance::get_instance().set_enabled(enable);
}

void
code_provenance_set_runtime_version(std::string_view runtime_version)
{
    Datadog::CodeProvenance::get_instance().set_runtime_version(runtime_version);
}

void
code_provenance_set_stdlib_path(std::string_view stdlib_path)
{
    Datadog::CodeProvenance::get_instance().set_stdlib_path(stdlib_path);
}

void
code_provenance_add_packages(std::unordered_map<std::string_view, std::string_view> distributions)
{
    Datadog::CodeProvenance::get_instance().add_packages(distributions);
}
