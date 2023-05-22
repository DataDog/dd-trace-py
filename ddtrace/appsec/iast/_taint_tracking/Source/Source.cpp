#include "Source.h"

string Source::toString() const {
    ostringstream ret;
    ret << "Source at " << this << " "
        << "[name=" << string(name) << ", value=" << string(value) << " origin=" << string(origin)
        << "]";
    return ret.str();
}

Source::operator std::string() const { return toString(); }

size_t Source::get_hash() const {
  size_t hname = hash<string>()(this->name);
  size_t hvalue = hash<string>()(this->value);
  size_t horigin = hash<string>()(this->origin);
  return hname ^ hvalue ^ horigin;
};

bool Source::eq(Source* other) const {
    return (strcmp(name, other->name) == 0) and
            (strcmp(value, other->value) == 0) and
            (strcmp(origin, other->origin) == 0);
}