#include "code_provenance_interface.hpp"

#include "code_provenance.hpp"

#include <string_view>

void
code_provenance_set_json_str(std::string_view json_str)
{
    Datadog::CodeProvenance::get_instance().set_json_str(json_str);
}
