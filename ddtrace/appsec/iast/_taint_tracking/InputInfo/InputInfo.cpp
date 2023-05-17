//
// Created by alberto.vara on 16/05/23.
//

#include "InputInfo.h"

const char *InputInfo::toString() const {
  ostringstream ret;
  ret << "InputInfo at " << this << " "
      << " [name=" << name << ", value=" << value << " origin=" << origin
      << "]";
  return ret.str().c_str();
}

InputInfo::operator std::string() const { return toString(); }

size_t InputInfo::get_hash() const {
  size_t hname = hash<string>()(this->name);
  size_t hvalue = hash<string>()(this->value);
  size_t horigin = hash<string>()(this->origin);
  return hname ^ hvalue ^ horigin;
};