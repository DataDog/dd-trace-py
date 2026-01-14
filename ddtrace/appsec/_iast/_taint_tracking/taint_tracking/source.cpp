#include <cstdlib>
#include <cstring>
#include <pybind11/pybind11.h>

#include "source.h"

using namespace std;
namespace py = pybind11;
using namespace pybind11::literals;

// Default truncation length if environment variable is not set
constexpr size_t DEFAULT_TRUNCATION_LENGTH = 250;

// Static variables for caching the truncation length
namespace {
size_t g_cached_truncation_length = 0;
}

// Get the truncation max length from environment variable
size_t
get_source_truncation_max_length()
{
    if (g_cached_truncation_length == 0) {
        const char* env_value = std::getenv("DD_IAST_TRUNCATION_MAX_VALUE_LENGTH");
        if (env_value != nullptr) {
            try {
                long parsed_value = std::strtol(env_value, nullptr, 10);
                if (parsed_value > 0) {
                    g_cached_truncation_length = static_cast<size_t>(parsed_value);
                } else {
                    g_cached_truncation_length = DEFAULT_TRUNCATION_LENGTH;
                }
            } catch (...) {
                g_cached_truncation_length = DEFAULT_TRUNCATION_LENGTH;
            }
        } else {
            g_cached_truncation_length = DEFAULT_TRUNCATION_LENGTH;
        }
    }

    return g_cached_truncation_length;
}

// Reset the cached truncation length (for testing purposes only)
void
reset_source_truncation_cache()
{
    g_cached_truncation_length = 0;
}

// Truncate value string if it exceeds the max length
string
truncate_source_value(string value)
{
    size_t max_length = get_source_truncation_max_length();
    if (value.length() > max_length) {
        return value.substr(0, max_length);
    }
    return value;
}

Source::Source(string name, string value, OriginType origin)
  : name(std::move(name))
  , value(truncate_source_value(std::move(value)))
  , origin(origin)
{
}

Source::Source(int name, string value, const OriginType origin)
  : name(origin_to_str(OriginType{ name }))
  , value(truncate_source_value(std::move(value)))
  , origin(origin)
{
}

string
Source::toString() const
{
    ostringstream ret;
    ret << "Source at " << this << " "
        << "[name=" << name << ", value=" << string(value) << ", origin=" << origin_to_str(origin) << "]";
    return ret.str();
}

Source::operator std::string() const
{
    return toString();
}

// Note: don't use size_t or long, if the hash is bigger than an int, Python
// will re-hash it!
int
Source::get_hash() const
{
    return std::hash<size_t>()(std::hash<string>()(name) ^ static_cast<long>(origin) ^ std::hash<string>()(value));
};

void
pyexport_source(py::module& m)
{
    m.def("origin_to_str", &origin_to_str, "origin"_a, py::return_value_policy::reference);
    m.def("str_to_origin", &str_to_origin, "origin"_a, py::return_value_policy::reference);
    py::enum_<TagMappingMode>(m, "TagMappingMode")
      .value("Normal", TagMappingMode::Normal)
      .value("Mapper", TagMappingMode::Mapper)
      .value("Mapper_Replace", TagMappingMode::Mapper_Replace)
      .export_values();

    py::enum_<OriginType>(m, "OriginType")
      .value("PARAMETER", OriginType::PARAMETER)
      .value("PARAMETER_NAME", OriginType::PARAMETER_NAME)
      .value("HEADER", OriginType::HEADER)
      .value("HEADER_NAME", OriginType::HEADER_NAME)
      .value("PATH", OriginType::PATH)
      .value("BODY", OriginType::BODY)
      .value("QUERY", OriginType::QUERY)
      .value("PATH_PARAMETER", OriginType::PATH_PARAMETER)
      .value("COOKIE", OriginType::COOKIE)
      .value("COOKIE_NAME", OriginType::COOKIE_NAME)
      .value("GRPC_BODY", OriginType::GRPC_BODY)
      .export_values();

    py::class_<Source>(m, "Source")
      .def(py::init<string, string, const OriginType>(), "name"_a = "", "value"_a = "", "origin"_a = OriginType())
      .def(py::init<int, string, const OriginType>(), "name"_a = "", "value"_a = "", "origin"_a = OriginType())
      .def_readonly("name", &Source::name)
      .def_readonly("origin", &Source::origin)
      .def_readonly("value", &Source::value)
      .def("to_string", &Source::toString)
      .def("__hash__",
           [](const Source& self) {
               return hash<string>{}(self.name + self.value) * (33 + static_cast<int>(self.origin));
           })
      .def("__str__", &Source::toString)
      .def("__repr__", &Source::toString)
      .def("__eq__", [](const Source* self, const Source* other) {
          if (other == nullptr)
              return false;
          return self->name == other->name && self->origin == other->origin && self->value == other->value;
      });
}