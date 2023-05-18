#include "Source.h"

const char *Source::toString() const {
  ostringstream ret;
  ret << "Source at " << this << " "
      << " [name=" << name << ", value=" << value << " origin=" << origin
      << "]";
  return ret.str().c_str();
}

Source::operator std::string() const { return toString(); }

size_t Source::get_hash() const {
  size_t hname = hash<string>()(this->name);
  size_t hvalue = hash<string>()(this->value);
  size_t horigin = hash<string>()(this->origin);
  return hname ^ hvalue ^ horigin;
};