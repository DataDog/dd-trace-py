#pragma once
#include "structmember.h"
#include <Python.h>
#include <iostream>
#include <pybind11/pybind11.h>
#include <sstream>
#include <string.h>

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
    COOKIE_NAME
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
    int refcount = 0;

    [[nodiscard]] string toString() const;

    inline void set_values(string name = "", string value = "", OriginType origin = OriginType())
    {
        this->name = move(name);
        this->value = move(value);
        this->origin = origin;
    }

    [[nodiscard]] int get_hash() const;

    static inline size_t hash(const string& name, const string& value, const OriginType origin)
    {
        return std::hash<size_t>()(std::hash<string>()(name + value) ^ (int)origin);
    };

    explicit operator std::string() const;
};

using SourcePtr = Source*;

inline string
origin_to_str(OriginType origin_type)
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
        default:
            return "";
    }
}

using SourcePtr = Source*;

void
pyexport_source(py::module& m);