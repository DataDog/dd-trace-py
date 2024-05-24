#pragma once
#include <sstream>

#include "../Constants.h"

// #define PY_MODULE_NAME_SOURCE PY_MODULE_NAME "." "Source"
using namespace std;
namespace py = pybind11;

enum class OriginType
{
    PARAMETER = 0,
    PARAMETER_NAME,
    HEADER,
    HEADER_NAME,
    PATH,
    BODY,
    QUERY,
    PATH_PARAMETER,
    COOKIE,
    COOKIE_NAME,
    GRPC_BODY,
    EMPTY
};

enum class TagMappingMode
{
    Normal,
    Mapper,
    Mapper_Replace
};

struct Source
{
    Source(string, string, OriginType);
    Source(int, string, OriginType);
    Source() = default;
    string name;
    string value;
    OriginType origin;

    [[nodiscard]] string toString() const;

    void set_values(string name_ = "", string value_ = "", OriginType origin_ = OriginType())
    {
        name = std::move(name_);
        value = std::move(value_);
        origin = origin_;
    }

    void reset()
    {
        name = "";
        value = "";
        origin = OriginType::EMPTY;
    }

    [[nodiscard]] int get_hash() const;

    static size_t hash(const string& name, const string& value, const OriginType origin)
    {
        return std::hash<size_t>()(std::hash<string>()(name + value) ^ (int)origin);
    };

    explicit operator std::string() const;
};

inline string
origin_to_str(const OriginType origin_type)
{
    switch (origin_type) {
        case OriginType::PARAMETER:
            return "http.request.parameter";
        case OriginType::PARAMETER_NAME:
            return "http.request.parameter.name";
        case OriginType::HEADER:
            return "http.request.header";
        case OriginType::HEADER_NAME:
            return "http.request.header.name";
        case OriginType::PATH:
            return "http.request.path";
        case OriginType::BODY:
            return "http.request.body";
        case OriginType::QUERY:
            return "http.request.query";
        case OriginType::PATH_PARAMETER:
            return "http.request.path.parameter";
        case OriginType::COOKIE_NAME:
            return "http.request.cookie.name";
        case OriginType::COOKIE:
            return "http.request.cookie.value";
        case OriginType::GRPC_BODY:
            return "http.request.grpc_body";
        default:
            return "";
    }
}

inline OriginType
str_to_origin(const string& origin_type_str)
{
    if (origin_type_str == "http.request.parameter")
        return OriginType::PARAMETER;
    if (origin_type_str == "http.request.parameter.name")
        return OriginType::PARAMETER_NAME;
    if (origin_type_str == "http.request.header")
        return OriginType::HEADER;
    if (origin_type_str == "http.request.header.name")
        return OriginType::HEADER_NAME;
    if (origin_type_str == "http.request.path")
        return OriginType::PATH;
    if (origin_type_str == "http.request.body")
        return OriginType::BODY;
    if (origin_type_str == "http.request.query")
        return OriginType::QUERY;
    if (origin_type_str == "http.request.path.parameter")
        return OriginType::PATH_PARAMETER;

    if (origin_type_str == "http.request.cookie.name")
        return OriginType::COOKIE_NAME;
    if (origin_type_str == "http.request.cookie.value")
        return OriginType::COOKIE;
    if (origin_type_str == "http.request.grpc_body")
        return OriginType::GRPC_BODY;

    return OriginType::PARAMETER;
}

void
pyexport_source(py::module& m);
