#include "Source.h"


Source::Source(string name, string value, OriginType origin)
: name(move(name)), value(move(value)), origin(origin) {}

string Source::toString() const {
    ostringstream ret;
    ret << "Source at " << this << " "
        << "[name=" << name << ", value=" << string(value) << ", origin=" << origin_to_str(origin)
        << "]";
    return ret.str();
}

Source::operator std::string() const { return toString(); }

size_t Source::get_hash() const {
    return std::hash<size_t>()(std::hash<string>()(name) ^ (long) origin ^ std::hash<string>()(value));
};

bool Source::eq(Source* other) const {
    return name == other->name and
            value == other->value and
            origin == other->origin;
}